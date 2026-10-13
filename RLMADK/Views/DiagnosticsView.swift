import SwiftUI

struct DiagnosticsView: View {
    @EnvironmentObject var model: PortalModel
    @State private var url: String = ""
    @FocusState private var urlFocused: Bool

    var body: some View {
        NavigationStack {
            List {
                Section("Backend") {
                    HStack {
                        TextField("http://host:8765", text: $url)
                            .textFieldStyle(.roundedBorder).font(.caption)
                            .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                            .focused($urlFocused)
                            .submitLabel(.done)
                            .onSubmit { urlFocused = false; model.setBackendURL(url) }
                            .accessibilityIdentifier("backendURLField")
                        Button("Apply") { urlFocused = false; model.setBackendURL(url) }.font(.caption)
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
                Section("Audio") {
                    Text("Owner: \(PortalModel.skipPermissionPrompts ? "not started (fixture mode: capture and recognition not requested)" : model.audioState.label)").font(.caption)
                    Text("Engine running: \(model.audio.isRunning ? "yes" : "no") · last buffer \(model.audio.lastBufferAge.map { String(format: "%.1f s ago", $0) } ?? "never") · write error \(model.audio.lastWriteError ?? "none")").font(.caption2)
                    Text(model.recognizerAvailability.label).font(.caption)
                    Text("Recognition: \(model.recognizer.continuous ? "continuous" : "stopped") · listening \(model.recognizer.isListening ? "yes" : "no") · restarts \(model.recognizer.restartCount) (\(model.recognizer.lastRestartReason))").font(.caption2)
                    if let p = model.progress { Text("Devotional progress: \(p.passage) @ \(p.revision.prefix(12)) resume \(p.resumeIndex + 1)/\(p.total)\(p.finished ? " finished" : "")").font(.caption2) }
                    Text("Mode persisted: quiet=\(model.mode.quiet ? "yes" : "no") activity=\(model.mode.label) devotional=\(model.mode.devotionalPassage ?? "-")@\(model.mode.devotionalPosition)").font(.caption2)
                }
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
                Section("Log") {
                    ForEach(model.log.prefix(60), id: \.self) { Text($0).font(.caption2) }
                }
            }
            .scrollDismissesKeyboard(.interactively)
            .navigationTitle("Diagnostics")
            .toolbar {
                ToolbarItemGroup(placement: .keyboard) {
                    Spacer()
                    Button("Done") { urlFocused = false }.accessibilityIdentifier("keyboardDoneDiagnostics")
                }
            }
            .onChange(of: model.keyboardDismissToken) { _, _ in urlFocused = false }
            .onChange(of: urlFocused) { _, focused in model.focusChanged(focused ? "backendURL" : nil) }
            .onAppear { url = model.backendURLString }
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
