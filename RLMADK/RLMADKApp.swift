import SwiftUI

/// App entry point for the RLM-ADK voice portal prototype.
@main
struct RLMADKApp: App {
    @StateObject private var model = PortalModel()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .onAppear { model.startAll() }
        }
    }
}
