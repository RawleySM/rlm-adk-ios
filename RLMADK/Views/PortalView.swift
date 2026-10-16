import SwiftUI

struct PortalView: View {
    @EnvironmentObject var model: PortalModel
    @State private var readMode = ProcessInfo.processInfo.environment["RLMADK_CI_READ_MODE"] == "1"
    @FocusState private var typedFocused: Bool

    var body: some View {
        NavigationStack {
            GeometryReader { geo in
                VStack(spacing: 8) {
                    if let notice = model.updatedNotice { updatedBanner(notice) }
                    if let offer = model.updateOffer { updateBanner(offer) }
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
                Text(model.bug.silencesOutput ? "Bug Reporting..." : model.mode.label).font(.subheadline.weight(.semibold)).lineLimit(1)
                    .foregroundStyle(model.bug.silencesOutput ? .red : .primary)
                    .accessibilityIdentifier("talkStatus")
                    .accessibilityValue("Voice: \(model.activeTalkTransport?.label ?? "-") \(model.talkState.label)")
                if model.botSpeaking || model.botAudible { Image(systemName: "speaker.wave.2").font(.caption) }
                Spacer(minLength: 4)
                if !model.bug.silencesOutput {
                    // Hidden while a bug report is open: the floor returns to its mode when the report ends.
                    Button(model.mode.isPraying ? "End prayer" : "Begin prayer") { model.dismissKeyboard(); model.mode.isPraying ? model.endPrayer() : model.beginPrayer() }
                        .buttonStyle(.borderedProminent).tint(.purple).controlSize(.small).accessibilityIdentifier("prayerButton")
                    Button(model.mode.quiet ? "Wake" : "Quiet") { model.dismissKeyboard(); model.mode.quiet ? model.wake() : model.enterQuiet() }
                        .buttonStyle(.borderedProminent).tint(model.mode.quiet ? .orange : .gray).controlSize(.small).accessibilityIdentifier("quietButton")
                }
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

    /// The conversation under the scripture: what was said and heard, and the reply, each labelled
    /// with its speaker ("You:" and the talk model's name).
    private var conversationPane: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 6) {
                if model.bug.phase != .idle { bugReportCard }
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
                if !model.assistantText.isEmpty {
                    Text("\(model.assistantName): \(model.assistantText)").font(.body)
                        .accessibilityIdentifier("assistantText")
                }
                if let err = model.lastError { Text(err).font(.caption2).foregroundStyle(.red) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
        }
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color(.secondarySystemBackground)))
        .scrollDismissesKeyboard(.interactively)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversationPane")
    }

    /// A newer private build is published: Install opens Apple's install prompt for it.
    private func updateBanner(_ offer: LatestBuild) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "arrow.down.app.fill").font(.title3)
            VStack(alignment: .leading, spacing: 1) {
                Text("Update available: build \(offer.build)").font(.subheadline.weight(.semibold))
                if let install = model.updateInstall {
                    Text(install.message).font(.caption2).lineLimit(2)
                        .accessibilityIdentifier("updateInstallMessage")
                } else if let notes = offer.notes, !notes.isEmpty {
                    Text(notes).font(.caption2).lineLimit(1)
                }
            }
            Spacer(minLength: 4)
            switch model.updateInstall?.phase {
            case .opening?, .prompt?, .suspended?:
                ProgressView().tint(.white).controlSize(.small)
            case .leaving?:
                Button("Stay") { model.stayInsteadOfInstalling() }
                    .buttonStyle(.bordered).tint(.white).controlSize(.small)
                    .accessibilityIdentifier("updateStayButton")
            case .noPrompt?:
                Button("Open page") { Task { await model.openUpdatePage() } }
                    .buttonStyle(.bordered).tint(.white).controlSize(.small)
                    .accessibilityIdentifier("updatePageButton")
            default:
                Button("Install") { Task { await model.installUpdate() } }
                    .buttonStyle(.bordered).tint(.white).controlSize(.small)
                    .accessibilityIdentifier("updateInstallButton")
            }
            Button { model.dismissUpdate() } label: { Image(systemName: "xmark") }
                .buttonStyle(.borderless).controlSize(.small)
                .accessibilityLabel("Dismiss update")
                .accessibilityIdentifier("updateDismissButton")
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.blue))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("updateBanner")
    }

    /// The first launch of a newer build: confirms the update landed.
    private func updatedBanner(_ notice: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.seal.fill").font(.title3)
            VStack(alignment: .leading, spacing: 1) {
                Text(notice).font(.subheadline.weight(.semibold))
                if let notes = model.updatedNotes, !notes.isEmpty {
                    Text(notes).font(.caption2).lineLimit(2)
                }
            }
            Spacer(minLength: 4)
            Button { model.dismissUpdatedNotice() } label: { Image(systemName: "xmark") }
                .buttonStyle(.borderless).controlSize(.small)
                .accessibilityLabel("Dismiss update notice")
                .accessibilityIdentifier("updatedDismissButton")
        }
        .foregroundStyle(.white)
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.green))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("updatedBanner")
    }

    /// The report the Bug chip is collecting (live lines while reporting), then its ticket.
    private var bugReportCard: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Image(systemName: "ladybug.fill")
                Text(model.bug.silencesOutput ? model.bug.chipLabel : "Bug report").font(.subheadline.weight(.semibold))
                Spacer()
                if !model.bug.silencesOutput {
                    Button("Dismiss") { model.dismissBugOutcome() }.font(.caption).accessibilityIdentifier("bugDismissButton")
                }
            }
            .foregroundStyle(.red)
            if model.bug.phase == .reporting {
                Text("Describe the problem, then tap Bug Reporting... to file it. The assistant stays silent; your voice is still recorded and transcribed.")
                    .font(.caption2).foregroundStyle(.secondary)
            }
            ForEach(Array(model.bug.lines.enumerated()), id: \.offset) { _, line in
                Text(line).font(.callout)
            }
            if !model.bug.interim.isEmpty {
                Text(model.bug.interim).font(.callout).italic().foregroundStyle(.secondary)
            } else if model.bug.phase == .reporting && model.bug.lines.isEmpty {
                Text("Listening…").font(.caption).foregroundStyle(.secondary)
            }
            if let outcome = model.bug.outcome {
                Text(outcome).font(.caption.weight(.medium)).foregroundStyle(model.bug.phase == .failed ? .red : .primary)
                    .accessibilityIdentifier("bugOutcome")
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Color.red.opacity(0.6)))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("bugReportCard")
    }

    /// The Bug chip: "Bug", then "Bug Reporting..." until the second tap files the report.
    private var bugChip: some View {
        Button { typedFocused = false; model.toggleBugReport() } label: {
            HStack(spacing: 4) {
                Image(systemName: "ladybug.fill")
                Text(model.bug.chipLabel).lineLimit(1)
            }
        }
        .buttonStyle(.borderedProminent)
        .tint(model.bug.silencesOutput ? .red : Color(.systemGray2))
        // "Bug Reporting..." is shown whole; the text field gives up the width.
        .fixedSize(horizontal: true, vertical: false)
        .layoutPriority(1)
        .disabled(model.bug.phase == .filing || model.mode.isPraying)
        .accessibilityIdentifier("bugButton")
        .accessibilityValue(model.bug.outcome ?? model.bug.chipLabel)
    }

    private var typedInput: some View {
        HStack(spacing: 8) {
            if !model.bug.silencesOutput {
                Button { model.beginListening() } label: { Image(systemName: "mic.fill") }
                    .buttonStyle(.borderedProminent).disabled(model.mode.isPraying)
                    .accessibilityLabel("Listen")
            }
            bugChip
            if model.bug.phase == .reporting {
                Button { model.cancelBugReport() } label: { Image(systemName: "xmark") }
                    .buttonStyle(.bordered).tint(.red).controlSize(.small)
                    .accessibilityLabel("Cancel bug report")
                    .accessibilityIdentifier("bugCancelButton")
            }
            TextField(model.bug.silencesOutput ? "Type to add to the report" : "Ask, or type a request", text: $model.typedInput)
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
