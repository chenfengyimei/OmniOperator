#ifndef OMNI_NATIVE_IDENTITY_H
#define OMNI_NATIVE_IDENTITY_H
#include <stdint.h>
int omni_process_identity(int32_t pid, uint32_t *uid, double *created);
int omni_audit_session(uint32_t *session);
int omni_graphical_session(uint32_t *audit_session, uint32_t *security_session);
int omni_registration_path_safe(const char *path);
int omni_system_registration_path_safe(const char *path);
int omni_inputs_released(int32_t state);
#endif

