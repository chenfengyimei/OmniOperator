// Identity is re-evaluated before and after each operation; no title-based window matching.
import AppKit
import ApplicationServices
import NativeIdentity

func sessionIdentity() throws -> String {
    guard getuid() > 0, let session = CGSessionCopyCurrentDictionary() as? Record,
          (session[kCGSessionOnConsoleKey as String] as? NSNumber)?.boolValue == true,
          (session[kCGSessionLoginDoneKey as String] as? NSNumber)?.boolValue == true,
          (session[kCGSessionUserIDKey as String] as? NSNumber)?.uint32Value == getuid(),
          (session["CGSSessionScreenIsLocked"] as? NSNumber)?.boolValue != true
    else { throw Fault.code("desktop_session_unverified") }
    var audit: UInt32 = 0
    try require(omni_audit_session(&audit) == 1, "desktop_session_unverified")
    return "macos:audit:\(audit):uid:\(getuid())"
}
func windowInfo(_ window: CGWindowID) throws -> Record {
    guard let windows = CGWindowListCopyWindowInfo([.optionIncludingWindow, .excludeDesktopElements], window) as? [Record],
          let info = windows.first,
          (info[kCGWindowNumber as String] as? NSNumber)?.uint32Value == window,
          (info[kCGWindowLayer as String] as? NSNumber)?.intValue == 0,
          (info[kCGWindowIsOnscreen as String] as? NSNumber)?.boolValue == true
    else { throw Fault.code("desktop_target_changed") }
    return info
}
func windowBounds(_ info: Record) throws -> CGRect {
    guard let dictionary = info[kCGWindowBounds as String] as? Record,
          let box = CGRect(dictionaryRepresentation: dictionary as CFDictionary),
          !box.isEmpty, !box.isInfinite, !box.isNull else { throw Fault.code("desktop_target_changed") }
    return box
}
func identity(_ window: CGWindowID) throws -> Target {
    let session = try sessionIdentity(), info = try windowInfo(window)
    guard let number = info[kCGWindowOwnerPID as String] as? NSNumber else { throw Fault.code("desktop_target_changed") }
    let pid = number.int32Value
    var uid: UInt32 = 0, created: Double = 0
    try require(omni_process_identity(pid, &uid, &created) == 1 && uid == getuid(), "desktop_permission_denied")
    guard let app = NSRunningApplication(processIdentifier: pid), !app.isTerminated,
          let application = app.bundleIdentifier ?? app.executableURL?.path, application.utf8.count <= 512
    else { throw Fault.code("desktop_target_changed") }
    return Target(window: window, pid: pid, created: created, application: application, session: session)
}
func verify(_ target: Target) throws {
    try require(same(try identity(target.window).record, target.record), "desktop_target_changed")
}
func capability(_ id: String, session: Bool, permission: Bool, implemented: Bool = true) -> Record {
    let available = session && permission && implemented
    return ["id": id, "supported": true, "installed": implemented,
            "permission": permission ? "granted" : "denied", "available": available,
            "error_code": available ? null : (implemented ? (session ? "desktop_permission_denied" : "desktop_session_unverified") : "desktop_adapter_not_implemented") as Any,
            "limitations": id == "foreground_control"
                ? ["默认关闭，逐次授权与审批；手动聚焦后只发送一个有界事件对。",
                   "需AX、输入监测及事件发送权限；目标不支持焦点通知或不能核验时拒绝。",
                   "事件已投递不代表任务成功；不能独立证明时保持unknown，不自动重放。"]
                : ["仅当前所有者已登录的图形会话；AX观察不授予写权限，截图不自动发送给模型。"],
            "recovery": available ? [] : ["在固定辅助应用的系统隐私设置中核验权限；无桌面会话可运行核心。"]]
}
func probe() -> Record {
    let session = try? sessionIdentity()
    let ax = AXIsProcessTrusted(), listen = CGPreflightListenEventAccess(), post = CGPreflightPostEventAccess()
    var foreground = capability("foreground_control", session: session != nil, permission: ax && listen && post)
    if !ax || !listen || !post {
        var recovery = [String]()
        if !ax { recovery.append("为固定桌面辅助应用允许辅助功能权限后重新检查。") }
        if !listen { recovery.append("为固定桌面辅助应用允许输入监测权限后重新检查。") }
        if !post { recovery.append("事件发送权限尚未允许；由所有者显式申请并重新检查。") }
        foreground["recovery"] = recovery
    }
    return ["version": protocolVersion, "platform": "darwin",
            "session": ["kind": session == nil ? "unknown" : "interactive", "identity": session as Any? ?? null],
            "capabilities": [
                capability("window_identity", session: session != nil, permission: ax),
                capability("accessibility", session: session != nil, permission: ax),
                capability("screen_capture", session: session != nil, permission: CGPreflightScreenCaptureAccess()),
                foreground],
            "acceptance": "pending"]
}
func targets(_ limit: Int) throws -> Record {
    let before = try sessionIdentity()
    try require(AXIsProcessTrusted(), "desktop_permission_denied")
    let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [Record] ?? []
    var items = [Record](), truncated = false
    for info in windows.prefix(4096) {
        guard let window = (info[kCGWindowNumber as String] as? NSNumber)?.uint32Value,
              let item = try? identity(window), item.session == before else { continue }
        if items.count >= limit { truncated = true; break }
        items.append(item.record)
    }
    try require(try sessionIdentity() == before, "desktop_target_changed")
    return ["items": items, "truncated": truncated || windows.count > 4096]
}

