// Read-only ServiceManagement inspection. No job submission/removal, shell or TCC prompt.
import Foundation
import ServiceManagement
import Darwin
import NativeIdentity

let serviceQueryVersion = "omni-macos-service-query-2"

private func serviceSession() throws -> String {
    var session: UInt32 = 0, security: UInt32 = 0
    try require(omni_graphical_session(&session, &security) == 1, "service_native_session_unverified")
    let identity: String
    do { identity = try sessionIdentity() }
    catch { throw Fault.code("service_native_session_unverified") }
    try require(identity == "macos:audit:\(session):uid:\(getuid())", "service_native_session_unverified")
    return identity + ":security:\(security)"
}

private func registrationPaths(_ label: String, system: Bool) throws {
    if system {
        for path in ["/", "/Library", "/Library/LaunchDaemons", "/Library/LaunchDaemons/" + label + ".plist"] {
            try require(path.withCString { omni_system_registration_path_safe($0) } == 1, "service_native_path_unverified")
        }
        return
    }
    guard let account = getpwuid(getuid()), let directory = account.pointee.pw_dir else {
        throw Fault.code("service_native_path_unverified")
    }
    let home = URL(fileURLWithPath: String(cString: directory), isDirectory: true)
    for suffix in ["", "Library", "Library/LaunchAgents", "Library/LaunchAgents/" + label + ".plist"] {
        let path = suffix.isEmpty ? home.path : home.appendingPathComponent(suffix).path
        try require(path.withCString { omni_registration_path_safe($0) } == 1, "service_native_path_unverified")
    }
}

private func queryIdentity(system: Bool) throws -> String {
    if !system { return try serviceSession() }
    // System services also run without Aqua. This entry only reads the system
    // domain as the ordinary owner; it never accepts a root/elevated caller.
    var owner: UInt32 = 0, created: Double = 0
    try require(getuid() > 0 && getuid() == geteuid() &&
        omni_process_identity(getpid(), &owner, &created) == 1 && owner == getuid(), "service_native_session_unverified")
    return "macos:system:uid:\(owner):process:\(getpid()):created:\(created)"
}

private func sameServiceJob(_ left: Record, _ right: Record) throws -> Bool {
    // NSDictionary equality treats true and 1 as equal. Native snapshots must
    // retain their typed spelling as well as their values.
    return try JSONSerialization.data(withJSONObject: left, options: [.sortedKeys]) ==
        JSONSerialization.data(withJSONObject: right, options: [.sortedKeys])
}

private func boundedServiceValue(_ value: Any, depth: Int = 0, count: inout Int, text: inout Int) throws {
    count += 1
    try require(depth <= 8 && count <= 1024, "service_native_record_limit")
    if let string = value as? String {
        text += string.utf8.count
        try require(string.utf8.count <= 4096 && text <= 32768, "service_native_record_limit")
    } else if let dictionary = value as? [String: Any] {
        try require(dictionary.count <= 64, "service_native_record_limit")
        for (key, item) in dictionary {
            try boundedServiceValue(key, depth: depth + 1, count: &count, text: &text)
            try boundedServiceValue(item, depth: depth + 1, count: &count, text: &text)
        }
    } else if let array = value as? [Any] {
        try require(array.count <= 64, "service_native_record_limit")
        for item in array { try boundedServiceValue(item, depth: depth + 1, count: &count, text: &text) }
    } else {
        try require(value is NSNumber || value is NSNull, "service_native_record_invalid")
    }
}

func serviceQuery(_ args: [String]) -> Never {
    guard args.count == 4, ["--service-query", "--system-service-query"].contains(args[0]) else { exit(2) }
    let system = args[0] == "--system-service-query"
    let label = args[1], uid = args[2], nonce = args[3]
    guard label.range(of: "^com\\.omnioperator\\.v2\\.[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", options: .regularExpression) != nil,
          uid == String(getuid()), getuid() == geteuid(), getuid() > 0,
          nonce.count == 32, nonce.allSatisfy({ "0123456789abcdef".contains($0) }) else { exit(2) }
    // The host owns the total five-second deadline; this fuse also covers host loss.
    DispatchQueue.global().asyncAfter(deadline: .now() + 10) { exit(4) }
    let domain = system ? kSMDomainSystemLaunchd : kSMDomainUserLaunchd
    var response: Record = ["version": serviceQueryVersion, "domain": system ? "system" : "gui/" + uid,
        "label": label, "uid": uid, "nonce": nonce,
        "complete": false, "loaded": null, "job": null, "error_code": "service_native_unverified"]
    do {
        let session = try queryIdentity(system: system)
        try registrationPaths(label, system: system)
        // These public inspection APIs are deprecated. They are deliberately
        // read-only and fail closed if a future macOS stops returning a complete
        // description; launchctl's human-readable print output is not a substitute.
        let selected = SMJobCopyDictionary(domain, label as CFString)?.takeRetainedValue()
        if let selected {
            try require(CFDictionaryGetCount(selected) <= 64, "service_native_record_limit")
            guard let job = selected as? Record,
                  let checked = SMJobCopyDictionary(domain, label as CFString)?.takeRetainedValue() else {
                throw Fault.code("service_native_changed")
            }
            var count = 0, text = 0
            try boundedServiceValue(job, count: &count, text: &text)
            try require(CFDictionaryGetCount(checked) <= 64, "service_native_record_limit")
            guard let checkedJob = checked as? Record else { throw Fault.code("service_native_record_invalid") }
            count = 0; text = 0
            try boundedServiceValue(checkedJob, count: &count, text: &text)
            try require((job["Label"] as? String) == label && (try sameServiceJob(job, checkedJob)), "service_native_changed")
            response["loaded"] = true
            response["job"] = job
        } else {
            // A nil single-job result could be a framework failure. Only a
            // successful complete enumeration can establish absence.
            guard let copied = SMCopyAllJobDictionaries(domain) else {
                throw Fault.code("service_native_unavailable")
            }
            let array = copied.takeRetainedValue()
            try require(CFArrayGetCount(array) <= 4096, "service_native_record_limit")
            guard let jobs = array as? [NSDictionary] else { throw Fault.code("service_native_record_invalid") }
            let matches = jobs.filter { ($0["Label"] as? String) == label }
            try require(matches.isEmpty, "service_native_changed")
            if let changed = SMJobCopyDictionary(domain, label as CFString) {
                _ = changed.takeRetainedValue()
                throw Fault.code("service_native_changed")
            }
            response["loaded"] = false
        }
        try require(try queryIdentity(system: system) == session, "service_native_changed")
        try registrationPaths(label, system: system)
        response["complete"] = true
        response["error_code"] = ""
    } catch {
        // Never emit exception text, other job descriptions, native stderr or UI content.
        if case Fault.code(let code) = error, code.hasPrefix("service_native_") {
            response["error_code"] = code
        }
        response["complete"] = false
        response["loaded"] = null
        response["job"] = null
    }
    do { try emit(response); exit(0) } catch { exit(2) }
}
