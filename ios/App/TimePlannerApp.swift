import SwiftUI

@main
struct TimePlannerApp: App {
    @StateObject private var model = RecorderModel()

    var body: some Scene {
        WindowGroup {
            ContentView().environmentObject(model)
        }
    }
}
