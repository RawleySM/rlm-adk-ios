import SwiftUI

struct PortalView: View {
    @EnvironmentObject var model: PortalModel
    @State private var showCitation: CitationDetail?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    statusHeader
                    modeControls
                    devotionalCard
                    conversationCard
                    typedInput
                }
                .padding()
            }
            .navigationTitle("Voice Portal")
            .sheet(item: Binding(get: { showCitation.map { IdentifiedCitation(detail: $0) } }, set: { showCitation = $0?.detail })) { item in
                CitationSheet(detail: item.detail)
            }
        }
    }

    private var statusHeader: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Circle().fill(model.connection.isConnected ? .green : .red).frame(width: 10, height: 10)
                Text(model.connection.label).font(.caption)
                Spacer()
                Circle().fill(model.audioState == .running ? .red : .gray).frame(width: 10, height: 10)
                Text(model.audioState.label).font(.caption)
            }
            HStack {
                Text("Mode: \(model.mode.label)").font(.headline)
                Spacer()
                if model.speaking { Label("Speaking", systemImage: "speaker.wave.2").font(.caption) }
            }
            Text(model.recognizerAvailability.label).font(.caption2).foregroundStyle(model.recognizerAvailability.usable ? .secondary : .orange)
        }
        .accessibilityIdentifier("statusHeader")
    }

    private var modeControls: some View {
        VStack(spacing: 8) {
            HStack {
                Button(model.mode.isPraying ? "End prayer" : "Begin prayer") { model.mode.isPraying ? model.endPrayer() : model.beginPrayer() }
                    .buttonStyle(.borderedProminent).tint(.purple)
                Button(model.mode.quiet ? "Wake" : "Quiet") { model.mode.quiet ? model.wake() : model.enterQuiet() }
                    .buttonStyle(.borderedProminent).tint(model.mode.quiet ? .orange : .gray)
                Button("Stop") { model.stopOutput() }.buttonStyle(.bordered)
            }
            HStack {
                Button { model.beginListening() } label: { Label("Listen", systemImage: "mic.fill") }
                    .buttonStyle(.borderedProminent).disabled(model.mode.isPraying)
                if !model.partialTranscript.isEmpty { Text(model.partialTranscript).font(.caption).italic().lineLimit(2) }
                Spacer()
            }
            if model.mode.isPraying {
                Text("Prayer is addressed to God. Long pauses never end it; tap End prayer when finished.")
                    .font(.caption).foregroundStyle(.purple)
            }
            if model.mode.quiet {
                Text("Quiet: no spoken output until you wake the portal. Recording and backend work continue.")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }

    private var devotionalCard: some View {
        GroupBox("Devotional") {
            if let d = model.devotional, d.available {
                VStack(alignment: .leading, spacing: 6) {
                    Text(d.title ?? d.passage).font(.subheadline.bold())
                    Text("revision \(d.revision_hash?.prefix(12) ?? "?") · position \(model.mode.devotionalPosition + 1)/\(d.segments?.count ?? 0) · teaching \(d.teaching_found == true ? "found" : "none")")
                        .font(.caption2).foregroundStyle(.secondary)
                    HStack {
                        Button(model.devotionalPlaying ? "Pause" : "Play") { model.devotionalPlaying ? model.pauseDevotional() : model.playDevotional() }.buttonStyle(.bordered)
                        Button("Reload") { Task { await model.loadDevotional() } }.buttonStyle(.bordered)
                    }
                    ForEach((d.segments ?? []).prefix(6)) { seg in
                        Button {
                            Task { showCitation = await model.citation(seg.cite_id) }
                        } label: {
                            HStack(alignment: .top) {
                                Text(seg.kind == "scripture" ? (seg.osis ?? "v") : "Teaching").font(.caption2).frame(width: 64, alignment: .leading)
                                Text(seg.text).font(.caption).lineLimit(3).multilineTextAlignment(.leading)
                            }
                        }.buttonStyle(.plain)
                    }
                }
            } else if let d = model.devotional {
                Text("Unavailable: \(d.reason ?? "?") (\(d.passage))").font(.caption).foregroundStyle(.orange)
            } else {
                HStack { Text("Not loaded").font(.caption); Button("Load") { Task { await model.loadDevotional() } } }
            }
        }
    }

    private var conversationCard: some View {
        GroupBox("Conversation") {
            VStack(alignment: .leading, spacing: 6) {
                if !model.lastTranscript.isEmpty { Text("You: \(model.lastTranscript)").font(.caption) }
                Text(model.assistantText.isEmpty ? "—" : model.assistantText).font(.body)
                    .accessibilityIdentifier("assistantText")
                if !model.turnInfo.isEmpty { Text(model.turnInfo).font(.caption2).foregroundStyle(.secondary) }
                if !model.citations.isEmpty {
                    ScrollView(.horizontal) {
                        HStack {
                            ForEach(model.citations) { c in
                                Button("[\(c.n ?? 0)] \(c.source_family)") { Task { showCitation = await model.citation(c.cite_id) } }
                                    .font(.caption2).buttonStyle(.bordered)
                            }
                        }
                    }
                }
                if let err = model.lastError { Text(err).font(.caption2).foregroundStyle(.red) }
            }
        }
    }

    private var typedInput: some View {
        HStack {
            TextField("Typed input (not live voice)", text: $model.typedInput).textFieldStyle(.roundedBorder)
            Button("Send") { model.submitTyped() }.disabled(model.typedInput.isEmpty || model.mode.isPraying)
        }
    }
}

struct IdentifiedCitation: Identifiable {
    var id: String { detail.cite_id }
    var detail: CitationDetail
}

struct CitationSheet: View {
    let detail: CitationDetail

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    if detail.available {
                        Text(detail.attribution ?? detail.source_family ?? "").font(.headline)
                        Text(detail.title ?? "").font(.subheadline)
                        Text("revision \(detail.revision_hash?.prefix(12) ?? "?") · verified \(detail.verified == true ? "yes" : "no") · current \(detail.current == true ? "yes" : "no")").font(.caption2).foregroundStyle(.secondary)
                        if let r = detail.recorded_at { Text("recorded \(r) (\(detail.recorded_at_basis ?? ""))").font(.caption2) }
                        if let o = detail.observed_at { Text("observed \(o)").font(.caption2) }
                        if let l = detail.source_locator { Text("locator \(l)").font(.caption2).foregroundStyle(.secondary) }
                        if let v = detail.verse_locators, !v.isEmpty { Text(v.map { $0.osis }.joined(separator: ", ")).font(.caption2) }
                        Divider()
                        Text(detail.text ?? "").font(.body)
                    } else {
                        Text("Citation unavailable: \(detail.reason ?? "?")").foregroundStyle(.orange)
                    }
                }.padding()
            }
            .navigationTitle("Evidence")
        }
    }
}
