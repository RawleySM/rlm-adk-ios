import SwiftUI

struct PortalView: View {
    @EnvironmentObject var model: PortalModel
    @State private var showCitation: CitationDetail?
    @State private var readMode = ProcessInfo.processInfo.environment["RLMADK_CI_READ_MODE"] == "1"
    @State private var showDevotional = false
    @FocusState private var typedFocused: Bool

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                VStack(spacing: 8) {
                    statusHeader
                    // Scripture front and centre; the conversation below it.
                    ScripturePaneView(readMode: false) { typedFocused = false; readMode = true }
                        .frame(height: max(260, geo.size.height * (typedFocused ? 0.42 : 0.6)))
                    conversationPane
                    typedInput
                }
                .padding(.horizontal, 10)
                .padding(.bottom, 6)
            }
            .toolbar(.hidden, for: .navigationBar)
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { typedFocused = false }.accessibilityIdentifier("keyboardDone")
                }
            }
            // Remote or programmatic dismissal (tab change, mode buttons, backend command) clears focus.
            .onChange(of: model.keyboardDismissToken) { _, _ in typedFocused = false }
            .onChange(of: typedFocused) { _, focused in model.focusChanged(focused ? "typedInput" : nil) }
            .sheet(item: Binding(get: { showCitation.map { IdentifiedCitation(detail: $0) } }, set: { showCitation = $0?.detail })) { item in
                CitationSheet(detail: item.detail)
            }
            .fullScreenCover(isPresented: $readMode) {
                ScripturePaneView(readMode: true) { readMode = false }
                    .environmentObject(model)
                    .background(ScriptureTheme.background.ignoresSafeArea())
                    .statusBarHidden(true)
            }
        }
    }

    /// One compact strip: connection, capture and voice status, the mode, and the floor buttons.
    private var statusHeader: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                Circle().fill(model.connection.isConnected ? .green : .red).frame(width: 8, height: 8)
                    .accessibilityLabel(model.connection.label)
                Circle().fill(model.audioState == .running ? .red : .gray).frame(width: 8, height: 8)
                    .accessibilityLabel(PortalModel.skipPermissionPrompts ? "Capture not requested (fixture mode)" : model.audioState.label)
                Circle().fill(model.talkState.isUp ? .green : .orange).frame(width: 8, height: 8)
                Text(model.mode.label).font(.subheadline.weight(.semibold)).lineLimit(1)
                    .accessibilityIdentifier("talkStatus")
                    .accessibilityValue("Voice: \(model.activeTalkTransport?.label ?? "-") \(model.talkState.label)")
                if model.botSpeaking || model.botAudible { Image(systemName: "speaker.wave.2").font(.caption) }
                Spacer(minLength: 4)
                Button(model.mode.isPraying ? "End prayer" : "Begin prayer") { model.dismissKeyboard(); model.mode.isPraying ? model.endPrayer() : model.beginPrayer() }
                    .buttonStyle(.borderedProminent).tint(.purple).controlSize(.small).accessibilityIdentifier("prayerButton")
                Button(model.mode.quiet ? "Wake" : "Quiet") { model.dismissKeyboard(); model.mode.quiet ? model.wake() : model.enterQuiet() }
                    .buttonStyle(.borderedProminent).tint(model.mode.quiet ? .orange : .gray).controlSize(.small).accessibilityIdentifier("quietButton")
                Button { model.dismissKeyboard(); model.stopOutput() } label: { Image(systemName: "stop.fill") }
                    .buttonStyle(.bordered).controlSize(.small).accessibilityLabel("Stop")
            }
            if model.mode.isPraying {
                Text("Prayer is addressed to God. Long pauses never end it; say amen and pause, or tap End prayer.")
                    .font(.caption2).foregroundStyle(.purple)
            }
            if model.mode.quiet {
                Text("Quiet: no spoken output until you wake the portal. Recording and backend work continue.")
                    .font(.caption2).foregroundStyle(.orange)
            }
            if let warning = model.captureWarning {
                Text(warning).font(.caption2).foregroundStyle(.red).accessibilityIdentifier("captureWarning")
            }
            if let err = model.lastJournalError {
                Text("Prayer text journal: \(err)").font(.caption2).foregroundStyle(.red)
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("statusHeader")
    }

    /// The conversation under the scripture: what was said and heard, the reply, and the devotional.
    private var conversationPane: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 6) {
                if !model.partialTranscript.isEmpty {
                    Text(model.partialTranscript).font(.caption).italic().foregroundStyle(.secondary)
                }
                if !model.lastTranscript.isEmpty {
                    Text("You: \(model.lastTranscript)").font(.callout.weight(.medium))
                }
                if !model.serverHeard.isEmpty {
                    Text("Heard: \(model.serverHeard)").font(.caption2).foregroundStyle(.secondary)
                        .accessibilityIdentifier("serverHeard")
                }
                Text(model.assistantText.isEmpty ? "—" : model.assistantText).font(.body)
                    .accessibilityIdentifier("assistantText")
                if let err = model.lastError { Text(err).font(.caption2).foregroundStyle(.red) }
                DisclosureGroup("Devotional", isExpanded: $showDevotional) { devotionalCard }
                    .font(.caption)
                    .padding(.top, 6)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
        }
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color(.secondarySystemBackground)))
        .scrollDismissesKeyboard(.interactively)
        .accessibilityIdentifier("conversationPane")
    }

    private var devotionalCard: some View {
        VStack(alignment: .leading) {
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

    private var typedInput: some View {
        HStack(spacing: 8) {
            Button { model.beginListening() } label: { Image(systemName: "mic.fill") }
                .buttonStyle(.borderedProminent).disabled(model.mode.isPraying)
                .accessibilityLabel("Listen")
            TextField("Ask, or type a request", text: $model.typedInput)
                .textFieldStyle(.roundedBorder)
                .focused($typedFocused)
                .submitLabel(.send)
                .onSubmit { typedFocused = false; model.submitTyped() }
                .accessibilityIdentifier("typedInput")
            Button("Send") { typedFocused = false; model.submitTyped() }
                .disabled(model.typedInput.isEmpty || model.mode.isPraying)
                .accessibilityIdentifier("sendButton")
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
