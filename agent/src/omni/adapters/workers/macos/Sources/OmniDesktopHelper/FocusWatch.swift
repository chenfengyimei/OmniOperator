// Target focus/session observers are owned by one request. Missing notifications deny takeover.
import AppKit
import ApplicationServices

final class FocusWatch {
    private let lock = NSLock()
    private var changes: UInt64 = 0, baseline: UInt64 = 0
    private var sessionLost = false
    private var observer: AXObserver?
    private var source: CFRunLoopSource?
    private var notifications = [String](), tokens = [NSObjectProtocol]()
    private let application: AXUIElement
    init(pid: pid_t) throws {
        application = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(application, 0.25)
        var installed = false
        desktopMain {
            let context = Unmanaged.passUnretained(self).toOpaque()
            let status = AXObserverCreate(pid, { _, _, _, context in
                if let context = context { Unmanaged<FocusWatch>.fromOpaque(context).takeUnretainedValue().changed() }
            }, &self.observer)
            if status == .success, let observer = self.observer {
                for name in [kAXFocusedUIElementChangedNotification, kAXFocusedWindowChangedNotification,
                             kAXApplicationDeactivatedNotification] {
                    if AXObserverAddNotification(observer, self.application, name as CFString, context) != .success { break }
                    self.notifications.append(name)
                }
                if self.notifications.count == 3 {
                    self.source = AXObserverGetRunLoopSource(observer)
                    CFRunLoopAddSource(CFRunLoopGetMain(), self.source, .commonModes)
                    installed = true
                }
            }
            let center = NSWorkspace.shared.notificationCenter
            for name in [NSWorkspace.didActivateApplicationNotification, NSWorkspace.didDeactivateApplicationNotification] {
                self.tokens.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in self?.changed() })
            }
            for name in [NSWorkspace.sessionDidResignActiveNotification, NSWorkspace.willSleepNotification] {
                self.tokens.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                    guard let self = self else { return }
                    self.lock.lock(); self.sessionLost = true; self.changes &+= 1; self.lock.unlock()
                })
            }
        }
        if !installed { _ = close(); throw Fault.code("desktop_monitor_unavailable") }
    }
    private func changed() { lock.lock(); changes &+= 1; lock.unlock() }
    var revision: UInt64 { lock.lock(); defer { lock.unlock() }; return changes }
    var healthy: Bool {
        lock.lock(); let active = !sessionLost; lock.unlock()
        return active && source.map { CFRunLoopSourceIsValid($0) } == true
    }
    func arm() { lock.lock(); baseline = changes; lock.unlock() }
    func unchanged() -> Bool {
        lock.lock(); let same = baseline == changes; lock.unlock()
        return same && healthy
    }
    @discardableResult func close() -> Bool {
        desktopMain {
            if let observer = self.observer {
                for name in self.notifications { AXObserverRemoveNotification(observer, self.application, name as CFString) }
            }
            var clean = true
            if let source = self.source {
                CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
                CFRunLoopSourceInvalidate(source); clean = !CFRunLoopSourceIsValid(source)
            }
            for token in self.tokens { NSWorkspace.shared.notificationCenter.removeObserver(token) }
            self.tokens.removeAll(); self.notifications.removeAll(); self.source = nil; self.observer = nil
            return clean
        }
    }
    deinit { _ = close() }
}

