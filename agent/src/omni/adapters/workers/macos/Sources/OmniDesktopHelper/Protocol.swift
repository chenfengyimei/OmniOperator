// One exact, bounded request. UI text and native exceptions never reach stderr.
import Foundation
import CoreGraphics

let protocolVersion = "omni-desktop-ipc-6"
let bundleID = "org.omnioperator.desktophelper"
let null = NSNull()
typealias Record = [String: Any]

enum Fault: Error {
    case code(String)
}
func require(_ condition: Bool, _ code: String = "desktop_invalid") throws {
    if !condition { throw Fault.code(code) }
}
func object(_ value: Any?, keys: Set<String>) throws -> Record {
    guard let result = value as? Record, Set(result.keys) == keys else { throw Fault.code("desktop_invalid") }
    return result
}
func integer(_ value: Any?, _ range: ClosedRange<Int>) throws -> Int {
    guard let number = value as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID(),
          number.doubleValue.isFinite, number.doubleValue == Double(number.intValue),
          range.contains(number.intValue) else { throw Fault.code("desktop_invalid") }
    return number.intValue
}
func string(_ value: Any?, maximum: Int, empty: Bool = false) throws -> String {
    guard let value = value as? String, value.unicodeScalars.count <= maximum,
          (empty || !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty),
          !value.unicodeScalars.contains(where: { $0.value < 32 || (127...159).contains($0.value) })
    else { throw Fault.code("desktop_invalid") }
    return value
}
func identifier(_ value: Any?) throws -> String {
    let text = try string(value, maximum: 36)
    try require(UUID(uuidString: text)?.uuidString.lowercased() == text)
    return text
}
func digest(_ value: Any?) throws -> String {
    let text = try string(value, maximum: 64)
    try require(text.count == 64 && text.allSatisfy { "0123456789abcdef".contains($0) })
    return text
}
func same(_ lhs: Record, _ rhs: Record) -> Bool {
    NSDictionary(dictionary: lhs).isEqual(to: rhs)
}
struct JsonKeys {
    let bytes: [UInt8]
    var index = 0
    mutating func whitespace() { while index < bytes.count && [9, 10, 13, 32].contains(bytes[index]) { index += 1 } }
    mutating func quoted() throws -> String {
        let start = index
        try require(index < bytes.count && bytes[index] == 34)
        index += 1
        while index < bytes.count {
            let current = bytes[index]; index += 1
            if current == 92 { try require(index < bytes.count); index += 1; continue }
            if current == 34 {
                guard let text = try JSONSerialization.jsonObject(with: Data(bytes[start..<index]), options: .fragmentsAllowed) as? String
                else { throw Fault.code("desktop_invalid") }
                return text
            }
        }
        throw Fault.code("desktop_invalid")
    }
    mutating func value(_ depth: Int = 0) throws {
        whitespace(); try require(depth <= 24 && index < bytes.count)
        let current = bytes[index]
        if current == 34 { _ = try quoted(); return }
        if current == 123 || current == 91 {
            let close: UInt8 = current == 123 ? 125 : 93
            index += 1; whitespace()
            if index < bytes.count && bytes[index] == close { index += 1; return }
            var keys = Set<String>()
            while true {
                whitespace()
                if current == 123 {
                    let key = try quoted(); try require(keys.insert(key).inserted)
                    whitespace(); try require(index < bytes.count && bytes[index] == 58); index += 1
                }
                try value(depth + 1); whitespace(); try require(index < bytes.count)
                let separator = bytes[index]; index += 1
                if separator == close { return }
                try require(separator == 44)
            }
        }
        let start = index
        while index < bytes.count && ![9, 10, 13, 32, 44, 93, 125].contains(bytes[index]) { index += 1 }
        try require(start < index)
    }
    mutating func check() throws { try value(); whitespace(); try require(index == bytes.count) }
}
func readFrame(maximum: Int = 65536) throws -> Record {
    var data = Data()
    while data.count < maximum {
        guard let byte = FileHandle.standardInput.readData(ofLength: 1).first else { throw Fault.code("desktop_invalid") }
        if byte == 10 {
            var keys = JsonKeys(bytes: Array(data)); try keys.check()
            guard !data.contains(13), let value = try JSONSerialization.jsonObject(with: data) as? Record else {
                throw Fault.code("desktop_invalid")
            }
            return value
        }
        data.append(byte)
    }
    throw Fault.code("desktop_invalid")
}
func emit(_ value: Record) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys, .fragmentsAllowed])
    try require(data.count < 65536, "desktop_response_too_large")
    FileHandle.standardOutput.write(data + Data([10]))
}
struct Target {
    let window: CGWindowID
    let pid: pid_t
    let created: Double
    let application: String
    let session: String
    init(_ value: Any?) throws {
        let raw = try object(value, keys: ["platform", "backend", "native_id", "pid", "process_created", "application_id", "session_id"])
        try require(raw["platform"] as? String == "darwin" && raw["backend"] as? String == "ax")
        guard let window = UInt32(try string(raw["native_id"], maximum: 20)), window > 0 else { throw Fault.code("desktop_invalid") }
        self.window = window
        pid = pid_t(try integer(raw["pid"], 1...Int(Int32.max)))
        if let wire = raw["process_created"] as? String {
            try require(wire.hasPrefix("ieee754:") && wire.count == 24)
            guard let bits = UInt64(wire.dropFirst(8), radix: 16) else { throw Fault.code("desktop_invalid") }
            created = Double(bitPattern: bits)
        } else if let number = raw["process_created"] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID() {
            created = number.doubleValue
        } else { throw Fault.code("desktop_invalid") }
        try require(created.isFinite && created > 0)
        application = try string(raw["application_id"], maximum: 512)
        session = try string(raw["session_id"], maximum: 512)
    }
    init(window: CGWindowID, pid: pid_t, created: Double, application: String, session: String) {
        self.window = window; self.pid = pid; self.created = created; self.application = application; self.session = session
    }
    var record: Record {
        ["platform": "darwin", "backend": "ax", "native_id": String(window), "pid": Int(pid),
         "process_created": created, "application_id": application, "session_id": session]
    }
}
struct Request {
    let id: String
    let operation: String
    let limit: Int
    let target: Target?
    let output: String?
    let action: Record?
    init(_ raw: Record) throws {
        operation = try string(raw["operation"], maximum: 32)
        try require(["probe", "targets", "verify", "observe", "capture", "act", "foreground"].contains(operation))
        var keys: Set<String> = ["version", "request_id", "operation", "target", "limit"]
        if operation == "capture" { keys.insert("output_path") }
        if operation == "act" || operation == "foreground" { keys.insert("action") }
        _ = try object(raw, keys: keys)
        try require(raw["version"] as? String == protocolVersion)
        id = try identifier(raw["request_id"])
        limit = try integer(raw["limit"], 1...50)
        if ["probe", "targets"].contains(operation) {
            try require(raw["target"] is NSNull && (operation != "probe" || limit == 1))
            target = nil
        } else {
            try require(limit == 1)
            target = try Target(raw["target"])
        }
        output = operation == "capture" ? try string(raw["output_path"], maximum: 4000) : nil
        if operation == "foreground" {
            action = try object(raw["action"], keys: ["kind", "text", "node", "policy", "takeover", "grant_id", "run_id", "operation_id"])
            try require(action?["policy"] as? String == "desktop-foreground-1")
            guard let consent = action?["takeover"] as? NSNumber,
                  CFGetTypeID(consent) == CFBooleanGetTypeID(), consent.boolValue else { throw Fault.code("desktop_invalid") }
            for key in ["grant_id", "run_id", "operation_id"] { _ = try identifier(action?[key]) }
        } else { action = operation == "act" ? try object(raw["action"], keys: ["kind", "text", "node", "policy"]) : nil }
    }
    func control(phase: String) -> Record {
        ["version": protocolVersion, "request_id": id, "operation": "foreground", "phase": phase,
         "grant_id": action!["grant_id"]!, "run_id": action!["run_id"]!, "operation_id": action!["operation_id"]!]
    }
    func response(_ result: Record?, code: String? = nil) -> Record {
        ["version": protocolVersion, "request_id": id, "operation": operation, "ok": code == nil,
         "result": result as Any? ?? null, "error_code": code as Any? ?? null]
    }
}

