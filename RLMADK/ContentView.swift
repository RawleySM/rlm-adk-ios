import SwiftUI

struct ContentView: View {
    @EnvironmentObject var model: PortalModel

    var body: some View {
        TabView {
            PortalView().tabItem { Label("Portal", systemImage: "waveform") }
            SourcesView().tabItem { Label("Sources", systemImage: "book") }
            WorkView().tabItem { Label("Work", systemImage: "tray.full") }
            DiagnosticsView().tabItem { Label("Diagnostics", systemImage: "stethoscope") }
        }
    }
}
