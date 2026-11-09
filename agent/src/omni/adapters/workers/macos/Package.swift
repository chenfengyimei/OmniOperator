// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "OmniDesktopHelper",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "OmniDesktopHelper", targets: ["OmniDesktopHelper"]),
               .executable(name: "OmniDesktopFixture", targets: ["OmniDesktopFixture"])],
    dependencies: [.package(path: "Core")],
    targets: [
        .target(name: "NativeIdentity", linkerSettings: [.linkedLibrary("bsm"), .linkedFramework("CoreGraphics"), .linkedFramework("Security")]),
        .executableTarget(name: "OmniDesktopHelper", dependencies: ["NativeIdentity", .product(name: "ForegroundCore", package: "core")]),
        .executableTarget(name: "OmniDesktopFixture", dependencies: ["NativeIdentity"]),
        .testTarget(name: "NativeIdentityTests", dependencies: ["NativeIdentity"]),
    ]
)

