import SwiftUI

/// Placeholder home screen. The rlm-adk connection over the tailnet comes later.
struct ContentView: View {
    private let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?"

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "point.3.connected.trianglepath.dotted")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("RLM-ADK")
                .font(.largeTitle.bold())
            Text("iPhone client \(version)")
                .foregroundStyle(.secondary)
        }
        .padding()
    }
}

#Preview {
    ContentView()
}
