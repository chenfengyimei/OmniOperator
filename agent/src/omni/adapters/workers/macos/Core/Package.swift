// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "OmniDesktopCore",
    products: [.library(name: "ForegroundCore", targets: ["ForegroundCore"])],
    targets: [
        .target(name: "ForegroundCore"),
        .testTarget(name: "ForegroundCoreTests", dependencies: ["ForegroundCore"]),
    ]
)

