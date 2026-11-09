// ScreenCaptureKit captures only the selected window; publish an RGB PNG through the host-owned file.
import AppKit
import ScreenCaptureKit
import ImageIO
import UniformTypeIdentifiers

func capture(_ target: Target, output: String) async throws -> Record {
    try verify(target)
    try require(CGPreflightScreenCaptureAccess(), "desktop_permission_denied")
    let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
    let matches = content.windows.filter { $0.windowID == target.window && $0.owningApplication?.processID == target.pid }
    try require(matches.count == 1, "desktop_target_changed")
    let window = matches[0], box = try windowBounds(windowInfo(target.window))
    try require(window.isOnScreen && !box.isEmpty && window.frame == box, "desktop_target_changed")
    let width = Int(ceil(box.width * 2)), height = Int(ceil(box.height * 2))
    try require(width > 0 && height > 0 && width <= 8192 && height <= 8192 && width * height <= 16000000, "desktop_capture_too_large")
    let filter = SCContentFilter(desktopIndependentWindow: window)
    let config = SCStreamConfiguration()
    config.width = width; config.height = height
    config.showsCursor = false; config.ignoreShadowsSingleWindow = true
    let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: config)
    try verify(target)
    try require(CGPreflightScreenCaptureAccess() && image.width == width && image.height == height, "desktop_capture_failed")
    guard let color = CGColorSpace(name: CGColorSpace.sRGB),
          let context = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4,
                                  space: color, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue)
    else { throw Fault.code("desktop_capture_failed") }
    context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
    guard let rgb = context.makeImage() else { throw Fault.code("desktop_capture_failed") }
    let data = NSMutableData()
    guard let destination = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
        throw Fault.code("desktop_capture_failed")
    }
    CGImageDestinationAddImage(destination, rgb, nil)
    try require(CGImageDestinationFinalize(destination) && data.length > 0 && data.length <= 16777216, "desktop_capture_too_large")
    let path = URL(fileURLWithPath: output)
    try require(path.path == output && path.isFileURL && path.lastPathComponent == "frame.png", "desktop_capture_failed")
    let parent = path.deletingLastPathComponent()
    let attributes = try FileManager.default.attributesOfItem(atPath: parent.path)
    try require(attributes[.type] as? FileAttributeType == .typeDirectory
                && (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid()
                && ((attributes[.posixPermissions] as? NSNumber)?.intValue ?? 0) & 0o077 == 0, "desktop_capture_failed")
    // Exclusive creation, no overwrite or link-following; host owns and removes this exact directory.
    let fd = open(output, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
    try require(fd >= 0, "desktop_capture_failed")
    let file = FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    try file.write(contentsOf: data as Data); try file.synchronize(); try file.close()
    try verify(target)
    return ["target": target.record, "width": width, "height": height, "bytes": data.length,
            "sha256": sha(data as Data), "method": "screen_capture_kit"]
}

