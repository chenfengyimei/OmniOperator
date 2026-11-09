// Passive input monitoring shared by semantic and foreground actions. Never records key contents.
import AppKit
import ApplicationServices

func desktopMain<T>(_ work: () -> T) -> T {
    Thread.isMainThread ? work() : DispatchQueue.main.sync(execute: work)
}
final class InputWatch {
    private let lock = NSLock()
    private var events: UInt64 = 0, baseline: UInt64 = 0
    private var failed = false
    private var taps = [CFMachPort](), sources = [CFRunLoopSource]()
    private let expected: [(Int64, CGEventType)]
    private var received = [Int64]()
    init(pid: pid_t? = nil, expected: [(Int64, CGEventType)] = []) throws {
        self.expected = expected
        try require(CGPreflightListenEventAccess(), "desktop_permission_denied")
        desktopMain {
            let types: [CGEventType] = [.keyDown, .keyUp, .flagsChanged, .leftMouseDown, .leftMouseUp,
                .rightMouseDown, .rightMouseUp, .otherMouseDown, .otherMouseUp, .mouseMoved,
                .leftMouseDragged, .rightMouseDragged, .otherMouseDragged, .scrollWheel]
            let mask = types.reduce(CGEventMask(0)) { $0 | (CGEventMask(1) << $1.rawValue) }
            let context = Unmanaged.passUnretained(self).toOpaque()
            let global = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap, options: .listenOnly,
                eventsOfInterest: mask, callback: { _, type, event, context in
                    if let context = context {
                        Unmanaged<InputWatch>.fromOpaque(context).takeUnretainedValue().record(type, event, target: false)
                    }
                    return Unmanaged.passUnretained(event)
                }, userInfo: context)
            self.install(global)
            if let pid = pid {
                let target = CGEvent.tapCreateForPid(pid: pid, place: .headInsertEventTap, options: .listenOnly,
                    eventsOfInterest: mask, callback: { _, type, event, context in
                        if let context = context {
                            Unmanaged<InputWatch>.fromOpaque(context).takeUnretainedValue().record(type, event, target: true)
                        }
                        return Unmanaged.passUnretained(event)
                    }, userInfo: context)
                self.install(target)
            }
        }
        if !healthy { _ = close(); throw Fault.code("desktop_monitor_unavailable") }
    }
    private func install(_ tap: CFMachPort?) {
        guard let tap = tap, let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0) else {
            failed = true
            if let tap = tap { CFMachPortInvalidate(tap) }
            return
        }
        taps.append(tap); sources.append(source)
        CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
        CGEvent.tapEnable(tap: tap, enable: true)
    }
    private func record(_ type: CGEventType, _ event: CGEvent, target: Bool) {
        lock.lock(); defer { lock.unlock() }
        if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput { failed = true; return }
        let marker = event.getIntegerValueField(.eventSourceUserData)
        if expected.contains(where: { $0.0 == marker && $0.1 == type }) {
            if target {
                // Only the target-PID tap acknowledges the two distinct event tokens.
                // The session tap may observe them too, but cannot double-count them.
                if received.count >= expected.count || expected[received.count].0 != marker { failed = true }
                else { received.append(marker) }
            }
        } else { events &+= 1 }
    }
    var revision: UInt64 { lock.lock(); defer { lock.unlock() }; return events }
    var healthy: Bool {
        lock.lock(); defer { lock.unlock() }
        return !failed && !taps.isEmpty && taps.allSatisfy { CFMachPortIsValid($0) && CGEvent.tapIsEnabled(tap: $0) }
    }
    var acknowledged: Bool {
        lock.lock(); defer { lock.unlock() }
        return !failed && !expected.isEmpty && received.count == expected.count
    }
    func arm() { lock.lock(); baseline = events; lock.unlock() }
    func unchanged() -> Bool {
        lock.lock(); let same = events == baseline; lock.unlock()
        return same && healthy
    }
    @discardableResult func close() -> Bool {
        desktopMain {
            var clean = true
            for source in self.sources {
                CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
                CFRunLoopSourceInvalidate(source); clean = clean && !CFRunLoopSourceIsValid(source)
            }
            for tap in self.taps {
                CGEvent.tapEnable(tap: tap, enable: false); CFMachPortInvalidate(tap)
                clean = clean && !CFMachPortIsValid(tap)
            }
            self.sources.removeAll(); self.taps.removeAll()
            return clean
        }
    }
    deinit { _ = close() }
}

