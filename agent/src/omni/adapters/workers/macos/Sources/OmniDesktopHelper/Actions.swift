// Semantic writes use the existing owner approval ledger and the shared passive monitor.
import AppKit
import ApplicationServices
import NativeIdentity

func currentFocus() throws -> AXUIElement {
    let system = AXUIElementCreateSystemWide()
    AXUIElementSetMessagingTimeout(system, 0.25)
    let focus = try element(ax(system, kAXFocusedUIElementAttribute))
    var pid: pid_t = 0, uid: UInt32 = 0, created: Double = 0
    try require(AXUIElementGetPid(focus, &pid) == .success
                && omni_process_identity(pid, &uid, &created) == 1 && uid == getuid(), "desktop_permission_denied")
    return focus
}
func receipt(_ status: String, _ code: String = "", verification: String = "not_verified") -> Record {
    ["status": status, "effect": status == "ok" ? "applied" : (status == "denied" ? "none" : "unknown"),
     "error_code": code, "verification": status == "denied" ? "not_dispatched" : verification]
}
func act(_ target: Target, action: Record) -> Record {
    var dispatched = false, watch: InputWatch?
    defer { watch?.close() }
    do {
        try require(action["policy"] as? String == "desktop-semantic-1")
        let kind = try string(action["kind"], maximum: 20), text = try string(action["text"], maximum: 512, empty: true)
        try require(["invoke", "select", "set_text"].contains(kind) && (kind == "set_text" || text.isEmpty))
        let node = try find(target, proof: action["node"])
        try require(node.actions().contains(kind), "desktop_permission_denied")
        watch = try InputWatch()
        // Arm only after the passive tap is installed: events before this point
        // (fixture activation, window-server focus churn) belong to the pre-checks,
        // not to the owner's intervention during this action.
        watch?.arm()
        // Background AX semantics do not take over the foreground. The focus that
        // existed after approval must remain unchanged, even if it belongs to the workbench.
        let focus = try currentFocus()
        try verify(target)
        // A monitor the system disabled is an unavailable guard, not owner input.
        try require(watch?.healthy == true, "desktop_monitor_unavailable")
        try require(watch?.unchanged() == true && AXIsProcessTrusted(), "desktop_user_intervened")
        let current = try find(target, proof: action["node"])
        try require(CFEqual(current.node, node.node) && watch?.unchanged() == true, "desktop_target_changed")
        try require(CFEqual(focus, try currentFocus()), "desktop_user_intervened")
        let status: AXError
        dispatched = true
        switch kind {
        case "invoke": status = AXUIElementPerformAction(node.node, kAXPressAction as CFString)
        case "select": status = AXUIElementSetAttributeValue(node.node, kAXSelectedAttribute as CFString, kCFBooleanTrue)
        default: status = AXUIElementSetAttributeValue(node.node, kAXValueAttribute as CFString, text as CFString)
        }
        try require(status == .success, "desktop_action_unverified")
        try verify(target)
        try require(CFEqual(focus, try currentFocus()), "desktop_user_intervened")
        var verification: String?
        if kind == "select" {
            let selected = try ax(node.node, kAXSelectedAttribute) as? NSNumber
            if selected?.boolValue == true { verification = "selected" }
        }
        if kind == "set_text",
           (try ax(node.node, kAXNumberOfCharactersAttribute) as? NSNumber)?.intValue == text.utf16.count,
           try boundedText(node.node) == text { verification = "value_matches" }
        try verify(target)
        try require(CFEqual(focus, try currentFocus()), "desktop_user_intervened")
        try require(watch?.unchanged() == true && AXIsProcessTrusted(), "desktop_user_intervened")
        if let verification = verification { return receipt("ok", verification: verification) }
        return receipt("unknown", "desktop_postcondition_unverified")
    } catch {
        let allowed: Set<String> = ["desktop_target_changed", "desktop_permission_denied",
                                   "desktop_user_intervened", "desktop_monitor_unavailable",
                                   "desktop_action_unverified"]
        let code: String
        if case Fault.code(let value) = error, allowed.contains(value) { code = value }
        else { code = "desktop_action_unverified" }
        return receipt(dispatched ? "unknown" : "denied", code)
    }
}

