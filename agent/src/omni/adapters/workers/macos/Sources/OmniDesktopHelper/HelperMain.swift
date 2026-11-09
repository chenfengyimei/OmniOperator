// The main run loop owns passive native callbacks; the one-shot worker performs bounded AX/SC work.
import AppKit
import ApplicationServices
import Darwin

@main
struct HelperMain {
    static func main() {
        guard Bundle.main.bundleIdentifier == bundleID,
              Bundle.main.object(forInfoDictionaryKey: "OmniDesktopProtocol") as? String == protocolVersion else { exit(2) }
        let args = Array(CommandLine.arguments.dropFirst())
        if args.first == "--service-query" || args.first == "--system-service-query" { serviceQuery(args) }
        if args.count == 2 && args[0] == "--request-permission" {
            // This entry is only invoked explicitly by the owner, never by probe or an API request.
            let allowed: Bool
            switch args[1] {
            case "accessibility":
                allowed = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
            case "screen": allowed = CGRequestScreenCaptureAccess()
            case "input": allowed = CGRequestListenEventAccess()
            case "post": allowed = CGRequestPostEventAccess()
            default: exit(2)
            }
            exit(allowed ? 0 : 3)
        }
        guard args == ["--ipc"] else { exit(2) }
        // Host owns the total deadline and process group. This fuse also covers a lost host.
        DispatchQueue.global().asyncAfter(deadline: .now() + 20) { exit(4) }
        Task.detached {
            do {
                let request = try Request(readFrame())
                do {
                    let result: Record
                    switch request.operation {
                    case "probe": result = probe()
                    case "targets": result = try targets(request.limit)
                    case "verify":
                        do {
                            try verify(request.target!)
                            result = ["matches": true, "target": request.target!.record, "reason": null]
                        } catch {
                            result = ["matches": false, "target": null, "reason": "desktop_target_changed"]
                        }
                    case "observe": result = try observe(request.target!)
                    case "capture": result = try await capture(request.target!, output: request.output!)
                    case "foreground": result = foreground(request)
                    default: result = act(request.target!, action: request.action!)
                    }
                    try emit(request.response(result))
                } catch {
                    let codes: Set<String> = ["desktop_target_changed", "desktop_permission_denied", "desktop_capture_unavailable",
                        "desktop_capture_too_large", "desktop_capture_failed", "desktop_observation_unavailable", "desktop_response_too_large"]
                    let code: String
                    if case Fault.code(let value) = error, codes.contains(value) { code = value }
                    else { code = "desktop_native_unavailable" }
                    try emit(request.response(nil, code: code))
                }
                exit(0)
            } catch { exit(2) }
        }
        RunLoop.main.run()
    }
}

