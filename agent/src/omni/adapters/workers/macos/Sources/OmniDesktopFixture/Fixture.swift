// Explicit acceptance fixture only. Never distributed inside the production helper application.
import AppKit
import NativeIdentity

@main
final class Fixture: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    var edit: NSTextField!
    var clicks = 0
    var directory: URL!
    var timer: Timer?
    static func main() {
        let owner = Fixture()
        let application = NSApplication.shared
        application.setActivationPolicy(.regular)
        application.delegate = owner
        withExtendedLifetime(owner) { application.run() }
    }
    func applicationDidFinishLaunching(_ notification: Notification) {
        guard CommandLine.arguments.count == 2 else { exit(2) }
        directory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
        window = NSWindow(contentRect: NSRect(x: 160, y: 180, width: 520, height: 250),
                          styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Omni isolated acceptance fixture"
        edit = NSTextField(frame: NSRect(x: 20, y: 170, width: 450, height: 28))
        edit.stringValue = "omni-owned-fixture"
        edit.setAccessibilityIdentifier("omni-fixture-editor")
        let secret = NSSecureTextField(frame: NSRect(x: 20, y: 120, width: 450, height: 28))
        secret.stringValue = "OMNI-PRIVATE-PASSWORD-CANARY"
        secret.setAccessibilityIdentifier("omni-fixture-password")
        let button = NSButton(title: "Owned fixture button", target: self, action: #selector(pressed))
        button.frame = NSRect(x: 20, y: 50, width: 300, height: 36)
        window.contentView?.addSubview(edit); window.contentView?.addSubview(secret); window.contentView?.addSubview(button)
        window.makeKeyAndOrderFront(nil)
        // This app is only launched by the explicit owner acceptance command.
        NSApplication.shared.activate(ignoringOtherApps: true)
        window.makeFirstResponder(edit)
        edit.currentEditor()?.selectedRange = NSRange(location: edit.stringValue.utf16.count, length: 0)
        var uid: UInt32 = 0, created: Double = 0, session: UInt32 = 0
        guard omni_process_identity(getpid(), &uid, &created) == 1 && omni_audit_session(&session) == 1 else { exit(2) }
        let target: [String: Any] = ["platform": "darwin", "backend": "ax", "native_id": String(window.windowNumber),
            "pid": Int(getpid()), "process_created": created,
            "application_id": "org.omnioperator.desktophelper.fixture",
            "session_id": "macos:audit:\(session):uid:\(uid)"]
        do {
            try JSONSerialization.data(withJSONObject: target).write(to: directory.appendingPathComponent("ready.json"), options: .atomic)
        } catch { exit(2) }
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in self?.tick() }
        DispatchQueue.main.asyncAfter(deadline: .now() + 120) { NSApplication.shared.terminate(nil) }
    }
    @objc func pressed() { clicks += 1 }
    func tick() {
        if FileManager.default.fileExists(atPath: directory.appendingPathComponent("stop").path) {
            NSApplication.shared.terminate(nil); return
        }
        let selection = edit.currentEditor()?.selectedRange ?? NSRange(location: NSNotFound, length: 0)
        let state: [String: Any] = ["text": edit.stringValue, "clicks": clicks,
            "selection_start": selection.location == NSNotFound ? -1 : selection.location,
            "selection_length": selection.length]
        if let data = try? JSONSerialization.data(withJSONObject: state) {
            try? data.write(to: directory.appendingPathComponent("state.json"), options: .atomic)
        }
    }
}

