import SwiftUI

struct DiagnosticsView: View {
    @EnvironmentObject var model: PortalModel
    @State private var url: String = ""
    @State private var talkURL: String = ""
    private enum Field: Hashable { case backend, talk }
    @FocusState private var focused: Field?

    var body: some View {
        NavigationStack {
            List {
                backendSection
                talkSection
                policySection
                audioSection
                capturesSection
                logSection
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("Diagnostics")
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { focused = nil }.accessibilityIdentifier("keyboardDoneDiagnostics")
                }
            }
            .onChange(of: model.keyboardDismissToken) { _, _ in focused = nil }
            .onChange(of: focused) { _, field in model.focusChanged(DiagnosticsView.focusName(field)) }
            .onAppear { url = model.backendURLString; talkURL = model.talkURLString }
        }
    }

    private var backendSection: some View {
        Section("Backend") {
            HStack {
                TextField("http://host:8765", text: $url)
                    .textFieldStyle(.roundedBorder).font(.caption)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .focused($focused, equals: .backend)
                    .submitLabel(.done)
                    .onSubmit { focused = nil; model.setBackendURL(url) }
                    .accessibilityIdentifier("backendURLField")
                Button("Apply") { focused = nil; model.setBackendURL(url) }.font(.caption)
            }
            Text("Device: \(model.installId.prefix(8)) · tab \(model.selectedTab) · keyboard \(model.keyboardVisible ? "up" : "down") · last snapshot \(model.lastSnapshotReason ?? "-")").font(.caption2)
            Text(model.connection.label).font(.caption)
            if let s = model.backendStatus {
                Text("\(s.service) \(s.version) commit \(s.source_commit?.prefix(8) ?? "?")").font(.caption2)
                if let i = s.inference { Text(inferenceLine(i)).font(.caption2) }
                if let p = s.prayer_intake { Text(intakeLine(p)).font(.caption2) }
                if let a = s.allocator { Text(allocatorLine(a)).font(.caption2) }
            }
            Button("Refresh status") { Task { await model.refreshStatus() } }.font(.caption)
        }
    }

    private var talkSection: some View {
        Section("Talk server (the app's voice)") {
            HStack {
                TextField("http://host:7860", text: $talkURL)
                    .textFieldStyle(.roundedBorder).font(.caption)
                    .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    .focused($focused, equals: .talk)
                    .submitLabel(.done)
                    .onSubmit { focused = nil; model.setTalkURL(talkURL) }
                    .accessibilityIdentifier("talkURLField")
                Button("Apply") { focused = nil; model.setTalkURL(talkURL) }.font(.caption)
            }
            Picker("Transport", selection: Binding(get: { model.selectedTalkTransport }, set: { model.setTalkTransport($0) })) {
                ForEach(TalkTransportKind.allCases, id: \.self) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityIdentifier("talkTransportPicker")
            Text("Running: \(model.activeTalkTransport?.label ?? "-") · \(model.talkState.label)").font(.caption)
            if let fallback = model.webrtcFallbackReason {
                Text("WebRTC fell back to WebSocket: \(fallback). Select WebRTC to try again.").font(.caption2).foregroundStyle(.orange)
            }
            if let detail = model.talk?.detail { Text(detail).font(.caption2) }
            if let voice = model.talk?.voice.statusLine { Text(voice).font(.caption2) }
            Text("Floor sent: \(model.lastClientModeSent?.rawValue ?? "-") · bot speaking \(model.botSpeaking ? "yes" : "no")").font(.caption2)
            Text("WebRTC spike: \(model.webrtcSpike)").font(.caption2).foregroundStyle(.secondary)
            Button("Reconnect") { model.connectTalk() }.font(.caption)
        }
    }

    private var policySection: some View {
        Section("Conversation policy") {
            Text("Source: \(model.policySource)").font(.caption2)
            Text("Address: \(model.policy.addressPhrases.joined(separator: ", "))").font(.caption2)
            Text("Amen: \(model.policy.amenPhrases.joined(separator: ", ")) · window \(String(format: "%.1f", model.policy.amenSilenceSeconds)) s · pre-roll \(String(format: "%.1f", model.policy.prayerPrerollSeconds)) s").font(.caption2)
            Button("Refetch policy") { Task { await model.refreshPolicy() } }.font(.caption)
        }
    }

    private var audioSection: some View {
        Section("Audio") {
            Text("Owner: \(PortalModel.skipPermissionPrompts ? "not started (fixture mode: capture and recognition not requested)" : model.audioState.label)").font(.caption)
            Text("Engine running: \(model.audio.isRunning ? "yes" : "no") · last buffer \(model.audio.lastBufferAge.map { String(format: "%.1f s ago", $0) } ?? "never") · write error \(model.audio.lastWriteError ?? "none")").font(.caption2)
            Text(model.recognizerAvailability.label).font(.caption)
            Text("Recognition: \(model.recognizer.continuous ? "continuous" : "stopped") · listening \(model.recognizer.isListening ? "yes" : "no") · restarts \(model.recognizer.restartCount) (\(model.recognizer.lastRestartReason))").font(.caption2)
            if let p = model.progress { Text("Devotional progress: \(p.passage) @ \(p.revision.prefix(12)) resume \(p.resumeIndex + 1)/\(p.total)\(p.finished ? " finished" : "")").font(.caption2) }
            Text("Mode persisted: quiet=\(model.mode.quiet ? "yes" : "no") activity=\(model.mode.label) devotional=\(model.mode.devotionalPassage ?? "-")@\(model.mode.devotionalPosition)").font(.caption2)
        }
    }

    private var capturesSection: some View {
        Section("Captures (local, retained)") {
            ForEach(model.captures) { c in
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(c.kind) \(c.captureId)").font(.caption.bold())
                    Text("\(c.status.label) · segments \(c.segments.count) · pending \(c.pendingUploads.count) · gaps \(c.gaps.count) · \(c.isClosed ? "closed" : "open")").font(.caption2)
                    ForEach(c.segments) { s in
                        Text("#\(s.index) \(s.bytes) B sha \(s.sha256.prefix(10)) \(s.verifiedUploaded ? "uploaded✓" : (s.lastError ?? "pending"))").font(.caption2).foregroundStyle(.secondary)
                    }
                    ForEach(c.gaps) { g in Text("gap \(g.kind): \(g.reason)").font(.caption2).foregroundStyle(.orange) }
                    if c.kind == "prayer" {
                        HStack {
                            Button("Retry uploads") { Task { await model.uploadPending(captureId: c.captureId) } }
                            Button("Refresh status") { Task { await model.refreshCapture(c.captureId) } }
                        }.font(.caption2).buttonStyle(.bordered)
                    }
                }
            }
        }
    }

    private var logSection: some View {
        Section("Log") {
            ForEach(model.log.prefix(60), id: \.self) { Text($0).font(.caption2) }
        }
    }

    private static func focusName(_ field: Field?) -> String? {
        switch field {
        case .backend: return "backendURL"
        case .talk: return "talkURL"
        case nil: return nil
        }
    }

    private func inferenceLine(_ i: BackendStatus.Inference) -> String {
        "Reasoning: \(i.model) @ \(i.base_url) · ready \(i.ready ? "yes" : "no") · phase \(i.model_phase ?? "?")"
    }

    private func intakeLine(_ p: BackendStatus.Intake) -> String {
        let detail = p.source_commit.map { String($0.prefix(8)) } ?? (p.error ?? "")
        return "Prayer intake: \(p.url) · reachable \(p.reachable ? "yes" : "no") · \(detail)"
    }

    private func allocatorLine(_ a: BackendStatus.Allocator) -> String {
        let runs = a.service_runs?.count ?? 0
        return "Allocator: available \(a.available ? "yes" : "no") · \(a.active ?? "?") · pid \(a.main_pid ?? 0) · runs \(runs)"
    }
}
