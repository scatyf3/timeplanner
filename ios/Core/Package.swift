// swift-tools-version:5.9
// TimePlannerCore — the iOS app's logic with no UI and no EventKit, so `swift test` runs on a Mac directly.
import PackageDescription

let package = Package(
    name: "TimePlannerCore",
    platforms: [.iOS(.v17), .macOS(.v13), .watchOS(.v10)],
    products: [
        .library(name: "TimePlannerCore", targets: ["TimePlannerCore"]),
    ],
    targets: [
        .target(name: "TimePlannerCore"),
        .testTarget(name: "TimePlannerCoreTests", dependencies: ["TimePlannerCore"]),
    ]
)
