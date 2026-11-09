// Pure, single-pair orchestration. No platform API, clock, I/O or ambient authority.
public enum TakeoverCode: String, Error {
    case invalid = "desktop_invalid"
    case focusTimeout = "desktop_focus_timeout"
    case revoked = "desktop_action_revoked"
    case userIntervened = "desktop_user_intervened"
    case targetChanged = "desktop_target_changed"
    case permissionDenied = "desktop_permission_denied"
    case inputUnverified = "desktop_input_unverified"
    case monitorUnavailable = "desktop_monitor_unavailable"
    case cleanupFailed = "desktop_monitor_cleanup_failed"
    case postconditionUnverified = "desktop_postcondition_unverified"
    case failed = "desktop_foreground_failed"
}
public struct InputCommand {
    public enum Kind: String { case typeText = "type_text", pressKey = "press_key", click = "click" }
    public static let keys = ["Enter", "Tab", "Escape", "Backspace", "Delete", "Home", "End", "Left", "Right", "Up", "Down"]
    public let kind: Kind
    public let text: String
    public init(kind: String, text: String) throws {
        guard let kind = Kind(rawValue: kind) else { throw TakeoverCode.invalid }
        switch kind {
        case .typeText:
            guard (1...128).contains(text.unicodeScalars.count),
                  !text.unicodeScalars.contains(where: { $0.value < 32 || (127...159).contains($0.value) })
            else { throw TakeoverCode.invalid }
        case .pressKey: guard Self.keys.contains(text) else { throw TakeoverCode.invalid }
        case .click: guard text.isEmpty else { throw TakeoverCode.invalid }
        }
        self.kind = kind; self.text = text
    }
}
public struct TakeoverReceipt {
    public let status: String
    public let effect: String
    public let errorCode: String
    public let verification: String
    public init(dispatched: Bool, error: TakeoverCode) {
        status = dispatched ? "unknown" : "denied"
        effect = dispatched ? "unknown" : "none"
        errorCode = error.rawValue
        verification = dispatched ? "not_verified" : "not_dispatched"
    }
    private init() {
        status = "ok"; effect = "applied"; errorCode = ""; verification = "text_appended"
    }
    static func verifiedText() -> Self { Self() }
}
public protocol TakeoverPort: AnyObject {
    var now: Double { get }
    var inputRevision: UInt64 { get }
    func prepare(_ command: InputCommand) throws
    func focusReady() throws -> Bool
    func released() -> Bool
    func sleep(_ seconds: Double)
    func arm() throws
    func authorize() throws -> Bool
    func check() throws
    func captureBefore() throws
    // Exactly one preallocated down/up pair. An implementation may only release
    // its own unmatched down during cleanup; it must never replay this pair.
    func send() throws
    func confirm() throws
    func verification() throws -> String?
    func close() -> Bool
}
public enum TakeoverController {
    public static func execute(_ command: InputCommand, port: TakeoverPort) -> TakeoverReceipt {
        var dispatched = false
        var result: TakeoverReceipt
        do {
            try port.prepare(command)
            let deadline = port.now + 10.0
            var quiet = port.now, revision = port.inputRevision
            while true {
                let ready = try port.focusReady(), next = port.inputRevision
                if next != revision || !ready || !port.released() { quiet = port.now; revision = next }
                else if port.now - quiet >= 0.25 { break }
                if port.now >= deadline { throw TakeoverCode.focusTimeout }
                port.sleep(0.01)
            }
            try port.arm()
            guard port.inputRevision == revision else { throw TakeoverCode.userIntervened }
            try port.check()
            guard try port.authorize() else { throw TakeoverCode.revoked }
            try port.check()
            try port.captureBefore()
            try port.check()
            dispatched = true
            try port.send()
            try port.confirm()
            try port.check()
            let verification = try port.verification()
            try port.check()
            result = command.kind == .typeText && verification == "text_appended"
                ? .verifiedText()
                : TakeoverReceipt(dispatched: true, error: .postconditionUnverified)
        } catch let code as TakeoverCode {
            result = TakeoverReceipt(dispatched: dispatched, error: code)
        } catch {
            result = TakeoverReceipt(dispatched: dispatched, error: .failed)
        }
        if !port.close() { result = TakeoverReceipt(dispatched: dispatched, error: .cleanupFailed) }
        return result
    }
}

