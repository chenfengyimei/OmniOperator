/* Public libproc and audit identity only; no AX handles or private WindowServer API. */
#include "NativeIdentity.h"
#include <libproc.h>
#include <bsm/libbsm.h>
#include <mach/mach.h>
#include <CoreGraphics/CoreGraphics.h>
#include <Security/AuthSession.h>
#include <sys/acl.h>
#include <sys/stat.h>
#include <sys/attr.h>
#include <sys/mount.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <string.h>

int omni_process_identity(int32_t pid, uint32_t *uid, double *created) {
    struct proc_bsdinfo info;
    memset(&info, 0, sizeof(info));
    if (pid <= 0 || proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, sizeof(info)) != sizeof(info)) return 0;
    if (info.pbi_pid != (uint32_t)pid || !info.pbi_start_tvsec) return 0;
    *uid = info.pbi_uid;
    *created = (double)info.pbi_start_tvsec + (double)info.pbi_start_tvusec / 1000000.0;
    return 1;
}
int omni_audit_session(uint32_t *session) {
    audit_token_t token;
    mach_msg_type_number_t count = TASK_AUDIT_TOKEN_COUNT;
    if (task_info(mach_task_self(), TASK_AUDIT_TOKEN, (task_info_t)&token, &count) != KERN_SUCCESS) return 0;
    *session = audit_token_to_asid(token);
    return *session != 0 && *session != (uint32_t)-1;
}
int omni_graphical_session(uint32_t *audit_session, uint32_t *security_session) {
    SecuritySessionId identity = 0;
    SessionAttributeBits attributes = 0;
    uint32_t audit = 0;
    if (SessionGetInfo(callerSecuritySession, &identity, &attributes) != errSessionSuccess ||
        !(attributes & sessionHasGraphicAccess) || (attributes & (sessionIsRemote | sessionIsRoot)) ||
        identity == noSecuritySession || identity == callerSecuritySession || !omni_audit_session(&audit)) return 0;
    /* Security and audit IDs belong to distinct public contracts. Preserve the
       pair; do not assume their numeric representations are interchangeable. */
    *audit_session = audit;
    *security_session = identity;
    return 1;
}
static int registration_identity_equal(const struct stat *left, const struct stat *right) {
    return left->st_dev == right->st_dev && left->st_ino == right->st_ino &&
        left->st_mode == right->st_mode && left->st_uid == right->st_uid && left->st_gid == right->st_gid &&
        left->st_nlink == right->st_nlink && left->st_size == right->st_size &&
        left->st_ctimespec.tv_sec == right->st_ctimespec.tv_sec &&
        left->st_ctimespec.tv_nsec == right->st_ctimespec.tv_nsec;
}
static int registration_acl_safe(acl_t acl) {
    acl_entry_t entry;
    int flag = ACL_FIRST_ENTRY, state = 0, count = 0, safe = 1;
    if (!acl || acl_valid(acl) != 0) return 0;
    for (;;) {
        errno = 0;
        state = acl_get_entry(acl, flag, &entry);
        /* Darwin returns 0 for an entry and -1/EINVAL at the end, unlike
           Linux's 1/0 convention. The ACL object was validated above. */
        if (state == -1 && errno == EINVAL) break;
        if (state != 0) { safe = 0; break; }
        flag = ACL_NEXT_ENTRY;
        acl_tag_t tag;
        /* Default macOS deny-delete ACLs are fine. Positive/unknown ACLs need
           explicit review; POSIX mode bits alone cannot prove their grants. */
        if (++count > 128 || acl_get_tag_type(entry, &tag) != 0 || tag != ACL_EXTENDED_DENY) {
            safe = 0;
            break;
        }
    }
    return safe;
}
static int registration_volume_safe(int descriptor) {
    struct statfs volume;
    struct attrlist attributes;
    struct { uint32_t length; vol_capabilities_attr_t value; } capabilities;
    memset(&attributes, 0, sizeof(attributes));
    memset(&capabilities, 0, sizeof(capabilities));
    attributes.bitmapcount = ATTR_BIT_MAP_COUNT;
    attributes.volattr = ATTR_VOL_INFO | ATTR_VOL_CAPABILITIES;
    if (fstatfs(descriptor, &volume) != 0 || !(volume.f_flags & MNT_LOCAL) ||
        fgetattrlist(descriptor, &attributes, &capabilities, sizeof(capabilities), 0) != 0 ||
        capabilities.length != sizeof(capabilities)) return 0;
    /* Slot 1 is the public interfaces capability group, not the format group.
       An unsupported/unknown security observation cannot prove ACL absence. */
    return (capabilities.value.valid[1] & VOL_CAP_INT_EXTENDED_SECURITY) &&
        (capabilities.value.capabilities[1] & VOL_CAP_INT_EXTENDED_SECURITY);
}
static int registration_path_safe(const char *path, uid_t owner) {
    struct stat before, secured, after;
    if (lstat(path, &before) != 0) return errno == ENOENT;
    if (before.st_uid != owner || (before.st_mode & 0022) ||
        (!S_ISDIR(before.st_mode) && (!S_ISREG(before.st_mode) || before.st_nlink != 1))) return 0;
    int descriptor = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
    if (descriptor < 0) return 0;
    filesec_t security = filesec_init();
    if (!security) { close(descriptor); return 0; }
    int present = 0, safe = 0;
    acl_t acl = NULL;
    /* acl_get_file's NULL/ENOENT conflates failed stat with absent metadata.
       A held no-follow descriptor on a local extended-security volume and a
       successful statx/property query prove absence instead. Retain the same
       descriptor and filesec object for capability, query and extraction. */
    if (registration_volume_safe(descriptor) && fstatx_np(descriptor, &secured, security) == 0 &&
        registration_identity_equal(&before, &secured) &&
        filesec_query_property(security, FILESEC_ACL, &present) == 0) {
        if (!present) safe = 1;
        else if (filesec_get_property(security, FILESEC_ACL, &acl) == 0) safe = registration_acl_safe(acl);
    }
    if (acl) acl_free(acl);
    filesec_free(security);
    close(descriptor);
    if (lstat(path, &after) != 0 || !registration_identity_equal(&before, &after)) return 0;
    return safe;
}
int omni_registration_path_safe(const char *path) { return registration_path_safe(path, getuid()); }
int omni_system_registration_path_safe(const char *path) { return registration_path_safe(path, 0); }
int omni_inputs_released(int32_t state) {
    if (state != kCGEventSourceStateHIDSystemState && state != kCGEventSourceStateCombinedSessionState) return 0;
    for (int key = 0; key < 128; ++key)
        if (CGEventSourceKeyState((CGEventSourceStateID)state, (CGKeyCode)key)) return 0;
    /* CGMouseButton names only three buttons; C casts also check held auxiliary buttons. */
    for (int button = 0; button < 32; ++button)
        if (CGEventSourceButtonState((CGEventSourceStateID)state, (CGMouseButton)button)) return 0;
    return 1;
}

