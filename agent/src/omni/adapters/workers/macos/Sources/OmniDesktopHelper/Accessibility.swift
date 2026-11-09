// Bounded AX paths are proofs to re-observe, never pointers or grants of authority.
import AppKit
import ApplicationServices
import CryptoKit

func sha(_ data: Data) -> String { SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined() }
func sha(_ text: String) -> String { sha(Data(text.utf8)) }
func ax(_ node: AXUIElement, _ attribute: String) throws -> CFTypeRef? {
    var value: CFTypeRef?
    let result = AXUIElementCopyAttributeValue(node, attribute as CFString, &value)
    if result == .attributeUnsupported || result == .noValue { return nil }
    try require(result == .success, "desktop_observation_unavailable")
    return value
}
func element(_ value: CFTypeRef?) throws -> AXUIElement {
    guard let value = value, CFGetTypeID(value) == AXUIElementGetTypeID() else { throw Fault.code("desktop_target_changed") }
    return unsafeBitCast(value, to: AXUIElement.self)
}
func axBounds(_ node: AXUIElement) throws -> CGRect? {
    guard let position = try ax(node, kAXPositionAttribute), let size = try ax(node, kAXSizeAttribute),
          CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else { return nil }
    var point = CGPoint.zero, extent = CGSize.zero
    try require(AXValueGetValue(unsafeBitCast(position, to: AXValue.self), .cgPoint, &point)
                && AXValueGetValue(unsafeBitCast(size, to: AXValue.self), .cgSize, &extent), "desktop_target_changed")
    let box = CGRect(origin: point, size: extent)
    try require([box.minX, box.minY, box.width, box.height].allSatisfy { $0.isFinite && abs($0) <= 10000000 }, "desktop_target_changed")
    return box
}
func childCount(_ node: AXUIElement, attribute: String = kAXChildrenAttribute) throws -> Int {
    var count: CFIndex = 0
    let status = AXUIElementGetAttributeValueCount(node, attribute as CFString, &count)
    if status == .attributeUnsupported || status == .noValue { return 0 }
    try require(status == .success && count >= 0, "desktop_observation_unavailable")
    return count
}
func children(_ node: AXUIElement, maximum: Int, attribute: String = kAXChildrenAttribute) throws -> [AXUIElement] {
    let count = min(try childCount(node, attribute: attribute), maximum)
    if count == 0 { return [] }
    var raw: CFArray?
    try require(AXUIElementCopyAttributeValues(node, attribute as CFString, 0, count, &raw) == .success,
                "desktop_observation_unavailable")
    guard let values = raw as? [AXUIElement], values.count == count else { throw Fault.code("desktop_observation_unavailable") }
    return values
}
func rootWindow(_ target: Target) throws -> AXUIElement {
    try verify(target)
    try require(AXIsProcessTrusted(), "desktop_permission_denied")
    let app = AXUIElementCreateApplication(target.pid)
    try require(AXUIElementSetMessagingTimeout(app, 0.25) == .success, "desktop_observation_unavailable")
    let expected = try windowBounds(windowInfo(target.window))
    let windows = try children(app, maximum: 128, attribute: kAXWindowsAttribute)
    var matches = [AXUIElement]()
    for window in windows {
        AXUIElementSetMessagingTimeout(window, 0.25)
        guard let box = try axBounds(window) else { continue }
        if abs(box.minX - expected.minX) <= 0.5 && abs(box.minY - expected.minY) <= 0.5
            && abs(box.width - expected.width) <= 0.5 && abs(box.height - expected.height) <= 0.5 { matches.append(window) }
    }
    try require(matches.count == 1, "desktop_target_changed")
    return matches[0]
}
func clean(_ text: String, maximum: Int = 512) -> String {
    let scalars = text.unicodeScalars.filter { $0.value >= 32 && !(127...159).contains($0.value) }
    return String(String.UnicodeScalarView(scalars.prefix(maximum)))
}
func boundedText(_ node: AXUIElement) throws -> String {
    // Never request the entire AXValue. Unsupported range readers return no text.
    let count = (try ax(node, kAXNumberOfCharactersAttribute) as? NSNumber)?.intValue ?? 0
    if count <= 0 { return "" }
    var range = CFRange(location: 0, length: min(count, 512))
    guard let parameter = AXValueCreate(.cfRange, &range) else { throw Fault.code("desktop_observation_unavailable") }
    var raw: CFTypeRef?
    let status = AXUIElementCopyParameterizedAttributeValue(node, kAXStringForRangeParameterizedAttribute as CFString, parameter, &raw)
    if status == .parameterizedAttributeUnsupported || status == .noValue { return "" }
    try require(status == .success, "desktop_observation_unavailable")
    return clean(raw as? String ?? "")
}
func exactText(_ node: AXUIElement) throws -> String? {
    guard let number = try ax(node, kAXNumberOfCharactersAttribute) as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID(), (0...512).contains(number.intValue) else { return nil }
    let count = number.intValue
    if count == 0 { return "" }
    var range = CFRange(location: 0, length: count)
    guard let parameter = AXValueCreate(.cfRange, &range) else { return nil }
    var raw: CFTypeRef?
    let status = AXUIElementCopyParameterizedAttributeValue(node, kAXStringForRangeParameterizedAttribute as CFString, parameter, &raw)
    if status == .parameterizedAttributeUnsupported || status == .noValue { return nil }
    try require(status == .success, "desktop_observation_unavailable")
    guard let text = raw as? String, text.utf16.count == count else { return nil }
    return text
}
func selectedTextRange(_ node: AXUIElement) throws -> CFRange? {
    guard let raw = try ax(node, kAXSelectedTextRangeAttribute), CFGetTypeID(raw) == AXValueGetTypeID() else { return nil }
    var range = CFRange(location: 0, length: 0)
    guard AXValueGetValue(unsafeBitCast(raw, to: AXValue.self), .cfRange, &range),
          range.location >= 0 && range.length >= 0 else { return nil }
    return range
}
struct Description {
    let node: AXUIElement
    let role: String
    let name: String
    let secure: Bool
    let enabled: Bool
    let box: CGRect
    let count: Int
    let descriptor: Record
    init(_ node: AXUIElement) throws {
        self.node = node
        var pid: pid_t = 0
        try require(AXUIElementGetPid(node, &pid) == .success, "desktop_target_changed")
        role = clean(try ax(node, kAXRoleAttribute) as? String ?? "AXUnknown", maximum: 80)
        let subrole = try ax(node, kAXSubroleAttribute) as? String ?? ""
        secure = subrole == kAXSecureTextFieldSubrole || role == kAXSecureTextFieldSubrole
        name = secure ? "" : clean(try ax(node, kAXTitleAttribute) as? String ?? "")
        enabled = (try ax(node, kAXEnabledAttribute) as? NSNumber)?.boolValue ?? false
        box = try axBounds(node) ?? .zero
        count = try childCount(node)
        let identifier = secure ? "" : (try ax(node, kAXIdentifierAttribute) as? String ?? "")
        descriptor = ["pid": Int(pid), "role": role, "name": sha(name), "identifier": sha(identifier),
                      "bounds": [box.minX, box.minY, box.width, box.height], "children": count, "secure": secure]
    }
    func proof(path: [Int], ancestry: [Record]) throws -> Record {
        ["backend": "ax", "path": path, "role": role, "name_sha256": sha(name),
         "ancestry_sha256": sha(try JSONSerialization.data(withJSONObject: ancestry + [descriptor], options: [.sortedKeys]))]
    }
    func supports(_ attribute: String) -> Bool {
        var settable: DarwinBoolean = false
        return AXUIElementIsAttributeSettable(node, attribute as CFString, &settable) == .success && settable.boolValue
    }
    func actions() -> [String] {
        if secure || !enabled || box.isEmpty || !CGPreflightListenEventAccess() { return [] }
        var names: CFArray?
        var result = [String]()
        if AXUIElementCopyActionNames(node, &names) == .success && (names as? [String] ?? []).contains(kAXPressAction) {
            result.append("invoke")
        }
        if supports(kAXSelectedAttribute) { result.append("select") }
        if [kAXTextFieldRole, kAXTextAreaRole].contains(role) && supports(kAXValueAttribute) { result.append("set_text") }
        if AXIsProcessTrusted() && CGPreflightPostEventAccess() {
            result.append("click")
            if supports(kAXFocusedAttribute) { result.append("press_key") }
            if [kAXTextFieldRole, kAXTextAreaRole].contains(role) && supports(kAXValueAttribute) { result.append("type_text") }
        }
        return result
    }
}
func observe(_ target: Target) throws -> Record {
    let root = try rootWindow(target), expectedBounds = try windowBounds(windowInfo(target.window))
    var nodes = [Record](), textBudget = 16384, truncated = false
    func text(_ value: String) -> String {
        var scalars = value.unicodeScalars.prefix(512)
        while String(String.UnicodeScalarView(scalars)).utf8.count > textBudget { scalars = scalars.dropLast(); truncated = true }
        let result = String(String.UnicodeScalarView(scalars))
        textBudget -= result.utf8.count
        return result
    }
    func visit(_ node: AXUIElement, path: [Int], parent: Int?, ancestry: [Record]) throws {
        if nodes.count >= 128 { truncated = true; return }
        AXUIElementSetMessagingTimeout(node, 0.25)
        let description = try Description(node), id = nodes.count + 1
        var nodePid: pid_t = 0
        try require(AXUIElementGetPid(node, &nodePid) == .success && nodePid == target.pid, "desktop_target_changed")
        let offscreen = description.box.isEmpty || !expectedBounds.intersects(description.box)
        nodes.append(["id": id, "parent": parent as Any? ?? null, "role": description.role,
                      "name": text(description.name), "text": description.secure ? "" : text(try boundedText(node)),
                      "password": description.secure, "enabled": description.enabled, "offscreen": offscreen,
                      "bounds": [description.box.minX, description.box.minY, description.box.width, description.box.height],
                      "reference": try description.proof(path: path, ancestry: ancestry),
                      "actions": offscreen ? [] : description.actions()])
        // Secure elements may expose child text in some providers. Never descend into them.
        if description.secure || path.count >= 7 { truncated = truncated || description.count > 0; return }
        let remaining = 128 - nodes.count
        let items = try children(node, maximum: remaining)
        truncated = truncated || description.count > items.count
        for (index, child) in items.enumerated() {
            try visit(child, path: path + [index], parent: id, ancestry: ancestry + [description.descriptor])
        }
    }
    try visit(root, path: [], parent: nil, ancestry: [])
    try verify(target)
    return ["target": target.record, "nodes": nodes, "truncated": truncated]
}
func find(_ target: Target, proof raw: Any?) throws -> Description {
    let proof = try object(raw, keys: ["backend", "path", "ancestry_sha256", "role", "name_sha256"])
    try require(proof["backend"] as? String == "ax")
    _ = try digest(proof["ancestry_sha256"]); _ = try digest(proof["name_sha256"])
    guard let rawPath = proof["path"] as? [Any], rawPath.count <= 7 else { throw Fault.code("desktop_invalid") }
    let path = try rawPath.map { try integer($0, 0...127) }
    var node = try rootWindow(target), ancestry = [Record]()
    for offset in path {
        let description = try Description(node), items = try children(node, maximum: 128)
        try require(!description.secure, "desktop_permission_denied")
        try require(offset < items.count, "desktop_target_changed")
        ancestry.append(description.descriptor); node = items[offset]
        AXUIElementSetMessagingTimeout(node, 0.25)
    }
    let description = try Description(node)
    try require(same(try description.proof(path: path, ancestry: ancestry), proof), "desktop_target_changed")
    return description
}

