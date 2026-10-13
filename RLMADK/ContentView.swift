import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: PortalModel
    /// `RLMADK_START_TAB` (portal|sources|work|diagnostics) lets CI screenshot a tab without UI automation.
    @State private var tab: String = ProcessInfo.processInfo.environment["RLMADK_START_TAB"] ?? "portal"

    var body: some View {
        TabView(selection: $tab) {
            PortalView().tabItem { Label("Portal", systemImage: "waveform") }.tag("portal")
            SourcesView().tabItem { Label("Sources", systemImage: "book") }.tag("sources")
            WorkView().tabItem { Label("Work", systemImage: "tray.full") }.tag("work")
            DiagnosticsView().tabItem { Label("Diagnostics", systemImage: "stethoscope") }.tag("diagnostics")
        }
    }
}
