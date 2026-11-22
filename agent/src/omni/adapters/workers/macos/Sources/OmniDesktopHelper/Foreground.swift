// One targeted CGEvent down/up pair after manual focus and the host's bound second permit.
import AppKit
import ApplicationServices
import ForegroundCore
import NativeIdentity

private func takeoverNative<T>(_ work: () throws -> T) throws -> T {
    do { return try work() }
    catch let code as TakeoverCode { throw code }
    catch Fault.code(let code) {
        if code == "desktop_session_unverified" { throw TakeoverCode.permissionDenied }
        throw TakeoverCode(rawValue: code) ?? .failed
    } catch { throw TakeoverCode.failed }
}
final class NativeTakeoverPort: TakeoverPort {
    private let request: Request
    private let target: Target
    private var command: InputCommand?
    private var selected: Description?
    private var down: CGEvent?, up: CGEvent?
    private var clickPoint: CGPoint?
    private var eventSource: CGEventSource?
    private var input: InputWatch?, focus: FocusWatch?
    private var downPosted = false, upPosted = false
    private var before: String?
    init(request: Request, target: Target) { self.request = request; self.target = target }
    var now: Double { ProcessInfo.processInfo.systemUptime }
    var inputRevision: UInt64 { (input?.revision ?? 0) &+ (focus?.revision ?? 0) }
    func sleep(_ seconds: Double) { Thread.sleep(forTimeInterval: max(0, seconds)) }
    private func permissions() throws {
        guard AXIsProcessTrusted(), CGPreflightListenEventAccess(), CGPreflightPostEventAccess()
        else { throw TakeoverCode.permissionDenied }
    }
    func prepare(_ command: InputCommand) throws {
        self.command = command
        try permissions()
        try takeoverNative {
            try verify(target)
            let selected = try find(target, proof: request.action?["node"])
            try require(selected.actions().contains(command.kind.rawValue), "desktop_permission_denied")
            self.selected = selected
            guard let source = CGEventSource(stateID: .privateState) else { throw TakeoverCode.permissionDenied }
            eventSource = source
            switch command.kind {
            case .click:
                let point = CGPoint(x: selected.box.midX, y: selected.box.midY)
                clickPoint = point
                try require(selected.box.contains(point) && windowBounds(windowInfo(target.window)).contains(point),
                            "desktop_target_changed")
                var hit: AXUIElement?
                let app = AXUIElementCreateApplication(target.pid)
                AXUIElementSetMessagingTimeout(app, 0.25)
                try require(AXUIElementCopyElementAtPosition(app, Float(point.x), Float(point.y), &hit) == .success
                            && hit.map { CFEqual($0, selected.node) } == true, "desktop_target_changed")
                down = CGEvent(mouseEventSource: source, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)
                up = CGEvent(mouseEventSource: source, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)
                down?.setIntegerValueField(.mouseEventClickState, value: 1)
                up?.setIntegerValueField(.mouseEventClickState, value: 1)
            case .pressKey, .typeText:
                let codes: [String: CGKeyCode] = ["Enter":36, "Tab":48, "Escape":53, "Backspace":51,
                    "Delete":117, "Home":115, "End":119, "Left":123, "Right":124, "Up":126, "Down":125]
                let key: CGKeyCode
                if command.kind == .typeText { key = 0 }
                else {
                    guard let mapped = codes[command.text] else { throw TakeoverCode.invalid }
                    key = mapped
                }
                down = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: true)
                up = CGEvent(keyboardEventSource: source, virtualKey: key, keyDown: false)
                if command.kind == .typeText {
                    let units = Array(command.text.utf16)
                    units.withUnsafeBufferPointer { buffer in
                        down?.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
                        up?.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress)
                    }
                }
            }
            guard let down = down, let up = up else { throw TakeoverCode.permissionDenied }
            let marker = Int64.random(in: 1..<(Int64.max - 1))
            down.flags = []; up.flags = []
            down.setIntegerValueField(.eventSourceUserData, value: marker)
            up.setIntegerValueField(.eventSourceUserData, value: marker + 1)
            input = try InputWatch(pid: target.pid, expected: [(marker, down.type), (marker + 1, up.type)])
            focus = try FocusWatch(pid: target.pid)
        }
    }
    private func focused() throws -> Bool {
        guard let selected = selected, let command = command else { throw TakeoverCode.invalid }
        let pid = desktopMain { NSWorkspace.shared.frontmostApplication?.processIdentifier }
        if pid != target.pid { return false }
        return try takeoverNative {
            let app = AXUIElementCreateApplication(target.pid)
            AXUIElementSetMessagingTimeout(app, 0.25)
            let window = try element(ax(app, kAXFocusedWindowAttribute))
            if !CFEqual(window, try rootWindow(target)) { return false }
            if command.kind == .click { return true }
            return CFEqual(try currentFocus(), selected.node)
        }
    }
    func focusReady() throws -> Bool {
        try permissions()
        guard input?.healthy == true, focus?.healthy == true else { throw TakeoverCode.monitorUnavailable }
        try takeoverNative { try verify(target) }
        return try focused()
    }
    private func released(_ state: CGEventSourceStateID) -> Bool {
        omni_inputs_released(state.rawValue) == 1
    }
    func released() -> Bool { released(.hidSystemState) && released(.combinedSessionState) }
    func arm() throws {
        guard input?.healthy == true, focus?.healthy == true else { throw TakeoverCode.monitorUnavailable }
        input?.arm(); focus?.arm()
    }
    func authorize() throws -> Bool {
        try takeoverNative {
            try emit(request.control(phase: "ready"))
            let received = try readFrame(maximum: 1024)
            return same(received, request.control(phase: "permit"))
        }
    }
    func check() throws {
        try permissions()
        guard input?.healthy == true, focus?.healthy == true else { throw TakeoverCode.monitorUnavailable }
        guard input?.unchanged() == true, focus?.unchanged() == true else { throw TakeoverCode.userIntervened }
        guard try focused(), (downPosted ? released(.hidSystemState) : released()) else { throw TakeoverCode.userIntervened }
        try takeoverNative {
            try verify(target)
            let current = try find(target, proof: request.action?["node"])
            try require(selected.map { CFEqual($0.node, current.node) } == true, "desktop_target_changed")
        }
    }
    func captureBefore() throws {
        guard command?.kind == .typeText, let selected = selected else { return }
        try takeoverNative {
            if let value = try exactText(selected.node), let range = try selectedTextRange(selected.node),
               range.length == 0 && range.location == value.utf16.count { before = value }
        }
    }
    func send() throws {
        guard let down = down, let up = up, !downPosted && !upPosted else { throw TakeoverCode.invalid }
        // There is one pair, no chunk loop. Posting is not a delivery/success receipt.
        if command?.kind == .click, let point = clickPoint {
            // A mouse pair posted directly to a PID carries windowNumber 0 on macOS and
            // is delivered to the process without being routed to any window. Post the
            // verified point through the window server instead: the target is already
            // frontmost, the point was AX hit-tested against the selected element, and
            // the cursor is restored right after the single pair.
            guard let saved = CGEvent(source: nil)?.location else { throw TakeoverCode.targetChanged }
            try takeoverNative {
                try verify(target)
                let bounds = try windowBounds(try windowInfo(target.window))
                guard bounds.contains(point) else { throw TakeoverCode.targetChanged }
            }
            CGWarpMouseCursorPosition(point)
            downPosted = true; down.post(tap: .cghidEventTap)
            upPosted = true; up.post(tap: .cghidEventTap)
            CGWarpMouseCursorPosition(saved)
        } else {
            downPosted = true; down.postToPid(target.pid)
            upPosted = true; up.postToPid(target.pid)
        }
    }
    func confirm() throws {
        let deadline = now + 0.2
        while input?.acknowledged != true {
            try check()
            if now >= deadline { throw TakeoverCode.inputUnverified }
            sleep(0.002)
        }
        sleep(0.01)
        try check()
    }
    func verification() throws -> String? {
        guard command?.kind == .typeText, let before = before, let command = command, let selected = selected,
              (before + command.text).utf16.count <= 512 else { return nil }
        return try takeoverNative {
            let expected = before + command.text
            guard try exactText(selected.node) == expected,
                  let range = try selectedTextRange(selected.node),
                  range.length == 0 && range.location == expected.utf16.count else { return nil }
            return "text_appended"
        }
    }
    func close() -> Bool {
        var clean = true
        // Only release this request's own unmatched down; never retry text or a click.
        if downPosted && !upPosted {
            do {
                try verify(target)
                if let up = up { upPosted = true; up.postToPid(target.pid) }
                else { clean = false }
            } catch { clean = false }
        }
        let inputClean = input?.close() ?? true, focusClean = focus?.close() ?? true
        input = nil; focus = nil; down = nil; up = nil; eventSource = nil; selected = nil; before = nil
        clickPoint = nil
        return clean && inputClean && focusClean
    }
    deinit { _ = close() }
}
func foreground(_ request: Request) -> Record {
    do {
        guard let action = request.action, let target = request.target else { throw TakeoverCode.invalid }
        let command = try InputCommand(kind: string(action["kind"], maximum: 20),
                                       text: string(action["text"], maximum: 128, empty: true))
        let result = TakeoverController.execute(command, port: NativeTakeoverPort(request: request, target: target))
        return ["status": result.status, "effect": result.effect, "error_code": result.errorCode, "verification": result.verification]
    } catch { return receipt("denied", "desktop_invalid") }
}

