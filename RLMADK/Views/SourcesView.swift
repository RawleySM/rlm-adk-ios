import SwiftUI

struct SourcesView: View {
    @EnvironmentObject var model: PortalModel
    @State private var query = ""
    @State private var hits: [SearchHit] = []
    @State private var detail: CitationDetail?

    var body: some View {
        NavigationStack {
            VStack {
                HStack {
                    TextField("Search prayers, teaching, Scripture", text: $query).textFieldStyle(.roundedBorder)
                    Button("Search") { Task { hits = await model.search(query) } }
                }.padding(.horizontal)
                if let counts = model.backendStatus?.counts {
                    Text(countsLine(counts)).font(.caption2).foregroundStyle(.secondary).padding(.horizontal)
                }
                List(hits) { hit in
                    Button {
                        Task { detail = await model.citation(hit.cite_id) }
                    } label: {
                        VStack(alignment: .leading) {
                            Text(hit.attribution ?? hit.source_family).font(.caption.bold())
                            Text(hit.title ?? "").font(.caption)
                            Text(hit.text).font(.caption2).lineLimit(3)
                            if let v = hit.verse_refs, !v.isEmpty { Text(v.joined(separator: ", ")).font(.caption2).foregroundStyle(.secondary) }
                        }
                    }
                }
            }
            .navigationTitle("Sources")
            .sheet(item: Binding(get: { detail.map { IdentifiedCitation(detail: $0) } }, set: { detail = $0?.detail })) { CitationSheet(detail: $0.detail) }
            .task { await model.refreshStatus() }
        }
    }

    private func countsLine(_ counts: [String: BackendStatus.CountValue]) -> String {
        let keys = counts.keys.sorted()
        return keys.map { key in "\(key): \(counts[key]?.text ?? "")" }.joined(separator: " · ")
    }
}
