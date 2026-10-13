import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: PortalModel

    /// The selected tab lives in the model so a backend `select_tab` command and the device snapshot
    /// see the same value. `RLMADK_START_TAB` (portal|sources|work|diagnostics) seeds it for CI.
    var body: some View {
        TabView(selection: $model.selectedTab) {
            PortalView().tabItem { Label("Portal", systemImage: "waveform") }.tag("portal")
            SourcesView().tabItem { Label("Sources", systemImage: "book") }.tag("sources")
            WorkView().tabItem { Label("Work", systemImage: "tray.full") }.tag("work")
            DiagnosticsView().tabItem { Label("Diagnostics", systemImage: "stethoscope") }.tag("diagnostics")
        }
    }
}
