// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ShieldVPNCore",
    platforms: [
        .iOS(.v16),
        .macOS(.v13)
    ],
    products: [
        .library(name: "ShieldVPNCore", targets: ["ShieldVPNCore"])
    ],
    targets: [
        .target(name: "ShieldVPNCore"),
        .testTarget(name: "ShieldVPNCoreTests", dependencies: ["ShieldVPNCore"])
    ]
)
