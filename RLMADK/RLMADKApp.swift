import SwiftUI

/// App entry point for the RLM-ADK voice portal prototype.
@main
struct RLMADKApp: App {
    @StateObject private var model = PortalModel()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(model)
                .onAppear {
                    // The XCTest host launches this same bundle; it must not request microphone or
                    // speech permissions, or the unanswered alert lingers on the simulator.
                    if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] == nil {
                        model.startAll()
                    }
                }
                .onChange(of: scenePhase) { _, phase in model.scenePhaseChanged(phase) }
        }
    }
}
