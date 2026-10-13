import AVFoundation
import CryptoKit
import Foundation
import SwiftUI
import UIKit

/// Orchestrates the single audio owner, on-device recognition, the gated speaker, the text/control
/// WebSocket, locally persisted captures and the durable work list. Mode is persisted independently
/// of any connection or session id.
@MainActor
final class PortalModel: ObservableObject {
    // Persisted
    @Published private(set) var mode: PortalMode
    @Published private(set) var captures: [CaptureManifest]
    @AppStorage("backendURL") var backendURLString: String = PortalModel.defaultBackendURL

    // Live
    @Published var connection: PortalClient.Connection = .disconnected(nil)
    @Published var audioState: AudioOwner.State = .idle
    @Published var recognizerAvailability: Recognizer.Availability
    @Published var partialTranscript: String = ""
    @Published var lastTranscript: String = ""
    @Published var assistantText: String = ""
    @Published var citations: [Citation] = []
    @Published var turnInfo: String = ""
    @Published var devotional: Devotional?
    @Published var devotionalPlaying = false
    @Published var work: [WorkOperation] = []
    @Published private(set) var localWork: [LocalWorkItem] = []
    @Published private(set) var progress: DevotionalProgress?
    @Published var backendStatus: BackendStatus?
    @Published var log: [String] = []
    @Published var speaking = false
    @Published var typedInput: String = ""
    @Published var lastError: String?

    // Phone inspection (app-owned state the backend may see) and keyboard facts
    /// Stable per-install identity for inspection; not an account, not a user.
    let installId: String
    @Published var selectedTab: String = ProcessInfo.processInfo.environment["RLMADK_START_TAB"] ?? "portal" {
        didSet { if selectedTab != oldValue { dismissKeyboard(); sendSnapshot(reason: "tab") } }
    }
    @Published private(set) var keyboardVisible = false
    @Published private(set) var keyboardFrame: CGRect?
    @Published private(set) var focusedField: String?
    /// Incremented to ask every focused field to resign (views observe it).
    @Published private(set) var keyboardDismissToken = 0
    @Published private(set) var scenePhaseLabel = "unknown"
    @Published private(set) var lastSnapshotReason: String?
    /// Set while a prayer is open and no audio buffer has reached the recorder recently; it is
    /// re-evaluated every second during prayer and cleared as soon as delivery resumes.
    @Published private(set) var captureWarning: String?
    @Published private(set) var lastJournalError: String?
    private var heartbeat: Timer?
    private var deliveryWatch: Timer?
    private var asrJournals: [String: PrayerASRJournal] = [:]
    /// Results already reported, by command id. A command pushed again (socket lost before the result
    /// reached the backend) is answered from here and never run twice. Persisted, bounded.
    private var completedCommands: [String: CommandResult] = [:]
    private var inFlightCommands: Set<String> = []
    /// Test seam: receives every command result the phone reports (also sent on the socket).
    var commandResultSink: ((CommandResult) -> Void)?
    /// Test seam: replaces `audio.isDelivering` (no engine runs under XCTest).
    var deliveryProbe: (() -> Bool)?
    private var isDelivering: Bool { deliveryProbe?() ?? audio.isDelivering }
    private var keyboardObservers: [NSObjectProtocol] = []

    let audio = AudioOwner()
    let recognizer: Recognizer
    let speaker = Speaker()
    private(set) var client: PortalClient
    private var phrases = PhraseQueue()
    private var ambientCaptureId: String
    private let store: LocalStore
    private var ciDemoDone = false
    private var silenceTimer: Timer?
    private var endpoint = EndpointClock()
    private var sessionId: String?

    /// Voice command phrases (configuration, recorded in the runbook). Buttons are the deterministic fallback.
    static let quietPhrases = ["quiet please", "be quiet", "go quiet"]
    static let wakePhrases = ["wake up", "portal wake"]
    static let prayerStartPhrases = ["begin prayer", "let us pray", "let's pray"]
    static let prayerEndPhrases = ["end prayer", "amen end prayer"]
    static let defaultBackendURL = ProcessInfo.processInfo.environment["RLMADK_BACKEND_URL"] ?? "http://100.90.105.30:8765"

    init() {
        let store = LocalStore()
        let loadedMode = store.loadMode() ?? PortalMode()
        var loadedCaptures = store.loadCaptures()
        let envURL = ProcessInfo.processInfo.environment["RLMADK_BACKEND_URL"]
        let urlString = envURL ?? UserDefaults.standard.string(forKey: "backendURL") ?? PortalModel.defaultBackendURL
        let ambient = loadedCaptures.first(where: { $0.kind == "ambient" && !$0.isClosed })?.captureId ?? CaptureManifest.newCaptureId(kind: "ambient")
        if !loadedCaptures.contains(where: { $0.captureId == ambient }) {
            loadedCaptures.insert(CaptureManifest(captureId: ambient, kind: "ambient", createdAt: Date()), at: 0)
        }
        for i in loadedCaptures.indices {
            // Files closed on disk but missing from the manifest (crash between close and save) are recovered.
            let found = store.closedSegmentFiles(loadedCaptures[i].captureId, knownIndexes: Set(loadedCaptures[i].segments.map(\.index)), excluding: nil)
            _ = loadedCaptures[i].reconcileFiles(found)
        }
        let relabelled = CaptureManifest.relabelRetainedZeroAudio(&loadedCaptures)
        let rec = Recognizer()
        self.store = store
        mode = loadedMode
        captures = loadedCaptures
        localWork = store.loadLocalWork()
        progress = store.loadProgress()
        recognizer = rec
        recognizerAvailability = rec.availability
        client = PortalClient(baseURL: URL(string: urlString) ?? URL(string: PortalModel.defaultBackendURL)!)
        ambientCaptureId = ambient
        installId = PortalModel.loadOrCreateInstallId()
        if envURL != nil { backendURLString = urlString }
        store.saveCaptures(captures)
        log = store.loadRecentLog()
        asrJournals = store.loadASRJournals()
        completedCommands = store.loadCommandResults()
        wire()
        observeKeyboard()
        for id in relabelled { logLine("retained prayer \(id) closed with no audio; relabelled Failed: no audio captured (nothing deleted)") }
        // Relaunch: mode (including quiet and an open prayer) is restored, never reset. A restored
        // open prayer owns every recognition request from the first one on.
        apply(.relaunch)
        if let restoredPrayer = mode.prayerCaptureId {
            recognizer.rebindOwner(restoredPrayer)
            if asrJournals[restoredPrayer] == nil { asrJournals[restoredPrayer] = PrayerASRJournal(captureId: restoredPrayer) }
        }
        logLine("launched; mode=\(mode.label) quiet=\(mode.quiet) prayer=\(mode.prayerCaptureId ?? "-")")
    }

    // MARK: wiring

    private func wire() {
        audio.onStateChange = { [weak self] s in Task { @MainActor in self?.audioStateChanged(s) } }
        audio.onBuffer = { [weak self] buffer, _ in self?.recognizer.append(buffer) }
        audio.onGap = { [weak self] gap in Task { @MainActor in self?.recordGap(gap) } }
        audio.onSegmentClosed = { [weak self] captureId, seg in Task { @MainActor in self?.segmentClosed(captureId: captureId, seg) } }
        audio.onWriteError = { [weak self] text in Task { @MainActor in self?.logLine(text); self?.sendSnapshot(reason: "write_error") } }
        recognizer.onAvailability = { [weak self] a in Task { @MainActor in self?.availabilityChanged(a) } }
        recognizer.onPartial = { [weak self] text in Task { @MainActor in self?.partial(text) } }
        recognizer.onFinal = { [weak self] text, owner in Task { @MainActor in self?.final(text, source: "voice", owner: owner) } }
        recognizer.onGap = { [weak self] gap in Task { @MainActor in self?.recordGap(gap) } }
        recognizer.onRestart = { [weak self] reason in Task { @MainActor in self?.recognitionRestarted(reason) } }
        speaker.onStateChange = { [weak self] s in Task { @MainActor in self?.speaking = s } }
        speaker.onUtteranceStarted = { [weak self] tag in Task { @MainActor in self?.devotionalSegmentStarted(tag) } }
        speaker.onUtteranceFinished = { [weak self] tag in Task { @MainActor in self?.devotionalSegmentFinished(tag) } }
        client.onConnection = { [weak self] c in Task { @MainActor in self?.connectionChanged(c) } }
        client.onEvent = { [weak self] e, raw in Task { @MainActor in self?.handle(e, raw) } }
    }

    private func logLine(_ s: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        let line = "\(stamp) \(s)"
        log.insert(line, at: 0)
        if log.count > 200 { log.removeLast() }
        store.appendLog(line) // durable, bounded; reloaded on launch
    }

    private func apply(_ event: PortalEvent) {
        let (next, effects) = PortalReducer.apply(event, to: mode)
        if next != mode {
            let modeChanged = next.quiet != mode.quiet || next.label != mode.label
            mode = next
            store.saveMode(mode)
            if modeChanged { sendSnapshot(reason: "mode") }
        }
        for effect in effects { perform(effect) }
    }

    private func perform(_ effect: PortalEffect) {
        switch effect {
        case .cancelOutput:
            phrases.cancelAll()
            speaker.stopAll()
            devotionalPlaying = false
        case .sendCancel(let turnId):
            client.send(["type": "cancel", "turn_id": turnId])
        case .startTurn(let turnId):
            phrases.beginTurn(turnId)
            assistantText = ""
            citations = []
            turnInfo = "turn \(turnId.prefix(8)) sent"
            var context: [String: Any] = [:]
            if let d = devotional, d.available {
                context["passage_title"] = d.title ?? d.passage
                context["passage_cite_ids"] = Array(Set((d.segments ?? []).map { $0.cite_id })).sorted()
            }
            client.send(["type": "final_transcript", "turn_id": turnId, "text": lastTranscript, "context": context])
        case .savePrayer(let captureId):
            finishCapture(captureId)
        case .waitForUser:
            logLine("prayer ended; capture settling (saved only once the writer reports its segments); waiting for the user")
        }
    }

    // MARK: lifecycle

    /// CI fixture/demo runs set `RLMADK_SKIP_PERMISSION_PROMPTS=1`: neither speech recognition nor the
    /// microphone is requested and the audio owner is not started (the simulator's microphone prompt
    /// cannot be answered unattended), so screenshots show the UI with the audio owner idle and the
    /// exact "live voice unavailable" condition. Real devices always prompt and start the owner.
    static let skipPermissionPrompts = ProcessInfo.processInfo.environment["RLMADK_SKIP_PERMISSION_PROMPTS"] == "1"
    static let underXCTest = ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil

    func startAll() {
        if PortalModel.skipPermissionPrompts {
            logLine("fixture mode: permission prompts skipped, audio owner not started, live voice not requested")
            var a = recognizerAvailability
            a.detail = "not requested (fixture mode)"
            availabilityChanged(a)
        } else {
            recognizer.requestAuthorization { [weak self] _ in Task { @MainActor in self?.startAudio() } }
            AVAudioSession.sharedInstance().requestRecordPermission { [weak self] _ in Task { @MainActor in self?.startAudio() } }
        }
        client.connect()
        Task { await refreshStatus() }
    }

    private func startAudio() {
        // The owner's own state is authoritative; the published mirror is updated through a queued Task.
        guard audio.state != .running else { reconcileRecognition(); return }
        let captureId = mode.prayerCaptureId ?? ambientCaptureId
        let startIndex = captures.first(where: { $0.captureId == captureId })?.nextSegmentIndex ?? 0
        do {
            try audio.start(captureId: captureId, directory: store.captureDirectory(captureId), startIndex: startIndex)
            logLine("audio owner running; capture \(captureId) from segment \(startIndex)")
        } catch {
            logLine("audio owner failed: \(error.localizedDescription)")
        }
        reconcileRecognition()
    }

    // MARK: recognition startup (VOICE-START-1)

    /// Whether continuous recognition has been requested for the current running audio session.
    private(set) var recognitionRequested = false
    /// Test seam: replaces `recognizer.startContinuous()` in controller tests. Returns nothing.
    var recognitionStarter: (() -> Void)?
    /// Authoritative audio state as last reported by the owner (not the queued published mirror).
    private var ownerRunning: Bool { audio.state == .running || audioState == .running }

    func audioStateChanged(_ state: AudioOwner.State) {
        audioState = state
        if state != .running { recognitionRequested = false }
        reconcileRecognition()
    }

    func availabilityChanged(_ availability: Recognizer.Availability) {
        var availability = availability
        if PortalModel.skipPermissionPrompts {
            availability.authorized = false
            availability.detail = "not requested (fixture mode)"
        }
        recognizerAvailability = availability
        if !availability.usable { recognitionRequested = false }
        reconcileRecognition()
    }

    /// The streaming cascade: recognition stays open between exchanges and is restarted after every
    /// final, no-speech or error without touching the recorder. This is driven from BOTH the audio
    /// running transition and the availability transition, so either callback order converges to one
    /// continuous recognition request and never a duplicate. Buttons remain the fallback.
    func reconcileRecognition() {
        guard ownerRunning, recognizerAvailability.usable else {
            if ownerRunning, !recognitionRequested {
                logLine("live voice unavailable here: \(recognizerAvailability.label); typed input and buttons remain")
            }
            return
        }
        guard !recognitionRequested else { return }
        recognitionRequested = true
        if let starter = recognitionStarter {
            starter()
        } else {
            recognizer.startContinuous()
        }
        endpoint.requestStarted()
        startSilenceTimer()
        logLine("continuous on-device recognition started")
    }

    func setBackendURL(_ s: String) {
        guard let url = URL(string: s) else { return }
        backendURLString = s
        client.setBaseURL(url)
    }

    private func connectionChanged(_ c: PortalClient.Connection) {
        let was = connection.isConnected
        connection = c
        if case .connected(let id) = c {
            sessionId = id
            client.send(["type": "hello", "mode": ["quiet": mode.quiet, "activity": mode.label], "install_id": installId, "app_version": PortalModel.appVersion, "build": PortalModel.appBuild])
            apply(.connectionRestored)
            logLine("connected session \(id.prefix(8)); mode stays \(mode.label)")
            sendSnapshot(reason: "connect")
            syncPrayerASR()
            Task {
                if devotional == nil { await loadDevotional() }
                await syncLocalWork()
                await refreshWork()
                await retryPendingUploads()
                if ProcessInfo.processInfo.environment["RLMADK_CI_DEMO"] == "1", !ciDemoDone {
                    // CI demo: a typed turn against the fixture backend so the screenshot shows streamed text.
                    ciDemoDone = true
                    typedInput = "What does the shepherd psalm say about rest?"
                    submitTyped()
                }
            }
        } else if was {
            apply(.connectionLost)
            logLine("connection lost; recording continues; mode stays \(mode.label)")
        }
    }

    // MARK: transcripts and turns

    func beginListening() {
        guard !mode.isPraying else { return }
        apply(.userSpeechStarted)
        partialTranscript = ""
        if recognizer.continuous { recognizer.start() } else { recognizer.startContinuous() }
        endpoint.requestStarted()
        startSilenceTimer()
        logLine(recognizerAvailability.usable ? "listening (on-device, manual restart)" : "listen requested but \(recognizerAvailability.label)")
    }

    private var utteranceInterrupted = false

    /// Recognition request restarted (after a final, no-speech or error): the silence window restarts
    /// too, so the fresh request is not end-pointed immediately by the previous utterance's age.
    func recognitionRestarted(_ reason: String) {
        endpoint.requestStarted()
        logLine("recognition restarted (\(reason))")
    }

    private func partial(_ text: String) {
        partialTranscript = text
        endpoint.speech()
        // Real speech interrupts whatever is playing or pending (response, trailing TTS, devotional),
        // once per utterance: a pending answer must not start speaking over the user's next utterance.
        if !utteranceInterrupted, !mode.isPraying, (speaker.isSpeaking || devotionalPlaying || mode.currentTurnId != nil) {
            utteranceInterrupted = true
            apply(.userSpeechStarted)
        }
        if let turnId = mode.currentTurnId { client.send(["type": "partial_transcript", "turn_id": turnId, "text": text]) }
    }

    /// Test seams for the recognizer paths (the recognizer is not available in the simulator).
    func partialTranscriptReceived(_ text: String) { partial(text) }
    /// `owner` is the capture id the recognition request was opened under (what the recognizer
    /// reports with every final); nil means a conversation request.
    func finalTranscriptReceived(_ text: String, owner: String? = nil) { final(text, source: "voice", owner: owner) }
    var endpointClock: EndpointClock { endpoint }

    private func final(_ text: String, source: String, owner: String? = nil) {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }
        partialTranscript = ""
        utteranceInterrupted = false
        lastTranscript = cleaned
        logLine("final (\(source)\(owner.map { ", prayer \($0)" } ?? "")): \(cleaned)")
        if let owner {
            // A request opened under a prayer capture: its text belongs to that capture, however
            // late it settles and whatever the mode is now. Journaled raw BEFORE any control phrase
            // acts (a line ending in "end prayer" is kept as recognized); never a turn. Control
            // phrases act only when that capture is still the open prayer: words from an ended
            // prayer never end or alter a newer one.
            journalPrayerASR(captureId: owner, text: cleaned)
            if owner == mode.prayerCaptureId {
                _ = handleVoiceCommand(cleaned)
            } else {
                logLine("final from ended prayer \(owner) journaled; control phrases not applied to the current mode")
            }
            return
        }
        if mode.isPraying {
            // A conversation request that settled after prayer began: not prayer text, not a turn,
            // and its control phrases do not touch the prayer.
            logLine("conversation final during prayer ignored (request opened before the prayer); control phrases not applied")
            return
        }
        if handleVoiceCommand(cleaned) { return }
        let turnId = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        apply(.finalTranscript(turnId: turnId))
    }

    /// Typed input is labelled as typed; it exercises the same turn path but is not proof of live voice.
    func submitTyped() {
        let text = typedInput
        typedInput = ""
        final(text, source: "typed")
    }

    func journalPrayerASR(captureId: String, text: String) {
        var journal = asrJournals[captureId] ?? PrayerASRJournal(captureId: captureId)
        let line = journal.append(text)
        asrJournals[captureId] = journal
        do {
            try store.appendASR(captureId: captureId, line: line)
        } catch {
            // The line stays in memory (unsynced) and is still sent; the disk failure is reported, not hidden.
            lastJournalError = "\(captureId) #\(line.sequence): \(error.localizedDescription)"
            logLine("prayer ASR journal write FAILED for \(captureId) #\(line.sequence): \(error.localizedDescription)")
        }
        syncPrayerASR(captureId: captureId)
    }

    /// Sends every unsynced journal line (all captures, or one) with capture id and sequence; the
    /// backend acknowledges the highest sequence it holds, which marks lines synced.
    func syncPrayerASR(captureId: String? = nil) {
        guard connection.isConnected else { return }
        for (id, journal) in asrJournals where captureId == nil || id == captureId {
            let pending = journal.unsynced
            guard !pending.isEmpty else { continue }
            client.send(["type": "prayer_asr", "capture_id": id, "items": pending.map { ["sequence": $0.sequence, "client_time": $0.client_time, "text": $0.text] }])
        }
    }

    func prayerASRJournal(_ captureId: String) -> PrayerASRJournal? { asrJournals[captureId] }

    private func handleVoiceCommand(_ text: String) -> Bool {
        let lower = text.lowercased()
        if PortalModel.quietPhrases.contains(where: { lower.hasSuffix($0) || lower == $0 }) { enterQuiet(); return true }
        if PortalModel.wakePhrases.contains(where: { lower.hasPrefix($0) || lower == $0 }) { wake(); return true }
        if !mode.isPraying, PortalModel.prayerStartPhrases.contains(where: { lower == $0 }) { beginPrayer(); return true }
        if mode.isPraying, PortalModel.prayerEndPhrases.contains(where: { lower.hasSuffix($0) }) { endPrayer(); return true }
        return false
    }

    private func startSilenceTimer() {
        silenceTimer?.invalidate()
        silenceTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                let silent = self.endpoint.silentFor()
                self.apply(.silence(seconds: silent)) // no-op by design; prayer never ends on silence
                if self.endpoint.shouldEndUtterance(listening: self.recognizer.isListening, praying: self.mode.isPraying) {
                    self.recognizer.endUtterance()
                }
            }
        }
    }

    func stopOutput() { apply(.stopRequested) }

    private func handle(_ e: ServerEvent, _ raw: [String: Any]) {
        switch e.type {
        case "assistant_text_delta":
            guard let turnId = e.turn_id, let text = e.text else { return }
            guard turnId == mode.currentTurnId else { return } // old-turn deltas never re-enter playback
            assistantText += text
            for phrase in phrases.accept(delta: text, turnId: turnId) { speakIfAllowed(phrase) }
            apply(.assistantDelta(turnId: turnId))
        case "assistant_done":
            guard let turnId = e.turn_id else { return }
            if turnId == mode.currentTurnId {
                for phrase in phrases.finish(turnId: turnId) { speakIfAllowed(phrase) }
                citations = e.citations ?? []
                turnInfo = "first delta \(e.first_delta_ms ?? -1) ms, done \(e.done_ms ?? -1) ms, model \(e.model ?? "?"), evidence \(e.evidence_count ?? 0)"
            }
            apply(.assistantDone(turnId: turnId))
        case "assistant_cancelled":
            if let turnId = e.turn_id { apply(.assistantCancelled(turnId: turnId)) }
        case "error":
            lastError = e.message
            logLine("backend error: \(e.message ?? "?")")
            if let turnId = e.turn_id { apply(.assistantCancelled(turnId: turnId)) }
        case "duplicate_turn":
            logLine("duplicate turn refused by backend: \(e.turn_id ?? "?")")
        case "command":
            handleCommand(raw)
        case "prayer_asr_ack":
            if let captureId = raw["capture_id"] as? String, let through = raw["max_sequence"] as? Int {
                asrJournals[captureId]?.markSynced(through: through)
                store.saveASRSync(asrJournals)
            }
        default:
            break
        }
    }

    private func speakIfAllowed(_ phrase: String) {
        guard SpeechGate.mayEnqueue(mode) else { return } // gate evaluated before enqueue
        speaker.speak(phrase)
    }

    // MARK: quiet / wake / prayer

    func enterQuiet() { apply(.enterQuiet); logLine("quiet entered (explicit)") }
    func wake() { apply(.wake); logLine("woken (explicit)") }

    func beginPrayer() {
        guard !mode.isPraying else { return }
        let captureId = CaptureManifest.newCaptureId(kind: "prayer")
        captures.insert(CaptureManifest(captureId: captureId, kind: "prayer", createdAt: Date()), at: 0)
        persistCaptures()
        asrJournals[captureId] = PrayerASRJournal(captureId: captureId)
        apply(.enterPrayer(captureId: captureId))
        // From here every recognition request belongs to this capture; an open conversation request
        // is ended and keeps its own identity.
        recognizer.rebindOwner(captureId)
        // The owner's intent (.running) is not evidence that audio arrives. Check the engine facts,
        // try one restart, and keep observing: the warning tracks delivery for the whole prayer.
        captureWarning = nil
        if audio.state == .running, !isDelivering {
            audio.restartIfNeeded(reason: "begin_prayer")
        } else if audio.state != .running {
            // One start attempt on a real device; never under the XCTest host (permission alert) or fixture mode.
            if !PortalModel.skipPermissionPrompts, !PortalModel.underXCTest { startAudio() }
        }
        if audio.state == .running { audio.beginCapture(captureId: captureId, directory: store.captureDirectory(captureId), startIndex: 0) }
        logLine("prayer begun; capture \(captureId); recognition continues for end-of-prayer commands only")
        refreshDeliveryWarning()
        deliveryWatch?.invalidate()
        deliveryWatch = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refreshDeliveryWarning() }
        }
        sendSnapshot(reason: "capture")
    }

    /// Observed every second while praying: states what was seen and when, and clears itself when
    /// buffers arrive again. Never a one-time verdict.
    func refreshDeliveryWarning() {
        guard mode.isPraying else { return }
        let stamp = ISO8601DateFormatter().string(from: Date())
        if isDelivering {
            if captureWarning != nil {
                captureWarning = nil
                logLine("audio delivery resumed at \(stamp); prayer capture is recording again")
                sendSnapshot(reason: "capture")
            }
            return
        }
        let detail = audio.state == .running
            ? "engine \(audio.isRunning ? "running" : "stopped"), last buffer \(audio.lastBufferAge.map { String(format: "%.1f s ago", $0) } ?? "never")"
            : audio.state.label
        let text = "Not recording as of \(stamp): no audio buffer reached the recorder in the last 2 s (\(detail)). Prayer text is still journaled."
        if captureWarning == nil {
            logLine(text)
            sendSnapshot(reason: "capture")
        }
        captureWarning = text
    }

    func endPrayer() {
        guard mode.isPraying else { return }
        // Ends the prayer's open recognition request without dropping its final (it is still
        // delivered under this capture's identity); later requests are conversation again.
        recognizer.rebindOwner(nil)
        apply(.endPrayer)
        deliveryWatch?.invalidate()
        deliveryWatch = nil
        captureWarning = nil
        logLine("prayer ended explicitly")
    }

    private func finishCapture(_ captureId: String) {
        guard let i = captures.firstIndex(where: { $0.captureId == captureId }) else { return }
        captures[i].finalizeRequestedAt = Date()
        persistCaptures()
        let ambientStart = captures.first(where: { $0.captureId == ambientCaptureId })?.nextSegmentIndex ?? 0
        if audio.state == .running {
            // Switch back to the ambient capture; the prayer writer settles asynchronously and only
            // then is the capture closed with its final segment count.
            audio.beginCapture(captureId: ambientCaptureId, directory: store.captureDirectory(ambientCaptureId), startIndex: ambientStart) { [weak self] produced in
                Task { @MainActor in self?.captureSettled(captureId, produced: produced) }
            }
        } else {
            captureSettled(captureId, produced: captures[i].segments.count)
        }
    }

    /// The writer reported its last segment; the manifest may now be closed and finalized.
    private func captureSettled(_ captureId: String, produced: Int) {
        guard let i = captures.firstIndex(where: { $0.captureId == captureId }) else { return }
        captures[i].settle(produced: produced)
        persistCaptures()
        if produced == 0 {
            logLine("capture \(captureId) settled with NO segments: nothing was recorded (\(captures[i].status.label))")
        } else {
            logLine("capture \(captureId) settled: \(produced) segments expected, \(captures[i].segments.count) recorded")
        }
        sendSnapshot(reason: "capture")
        Task { await self.uploadPending(captureId: captureId) }
    }

    /// Test seam for the settle path (the writer is not available in the simulator).
    func captureSettledForTest(_ captureId: String, produced: Int) { captureSettled(captureId, produced: produced) }

    private func segmentClosed(captureId: String, _ seg: SegmentRecord) {
        guard let i = captures.firstIndex(where: { $0.captureId == captureId }) else { return }
        if !captures[i].segments.contains(where: { $0.index == seg.index }) { captures[i].segments.append(seg) }
        persistCaptures()
        logLine("segment \(seg.index) closed for \(captureId): \(seg.bytes) bytes sha \(seg.sha256.prefix(10))")
        sendSnapshot(reason: "capture")
        if captures[i].kind == "prayer" { Task { await uploadPending(captureId: captureId) } }
    }

    private func recordGap(_ gap: GapRecord) {
        let captureId = mode.prayerCaptureId ?? ambientCaptureId
        if let i = captures.firstIndex(where: { $0.captureId == captureId }) {
            captures[i].gaps.append(gap)
            persistCaptures()
        }
        logLine("gap (\(gap.kind)): \(gap.reason)")
    }

    private func persistCaptures() { store.saveCaptures(captures) }

    // MARK: phone inspection: snapshot, keyboard, remote commands, recovery transfers

    static var appVersion: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?" }
    static var appBuild: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "?" }

    private static func loadOrCreateInstallId() -> String {
        if let existing = UserDefaults.standard.string(forKey: "installId"), !existing.isEmpty { return existing }
        let fresh = "ios-" + UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(16).lowercased()
        UserDefaults.standard.set(fresh, forKey: "installId")
        return fresh
    }

    private func observeKeyboard() {
        let center = NotificationCenter.default
        keyboardObservers.append(center.addObserver(forName: UIResponder.keyboardDidShowNotification, object: nil, queue: .main) { [weak self] note in
            let frame = (note.userInfo?[UIResponder.keyboardFrameEndUserInfoKey] as? NSValue)?.cgRectValue
            Task { @MainActor in self?.keyboardChanged(visible: true, frame: frame) }
        })
        keyboardObservers.append(center.addObserver(forName: UIResponder.keyboardDidHideNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.keyboardChanged(visible: false, frame: nil) }
        })
    }

    func keyboardChanged(visible: Bool, frame: CGRect?) {
        keyboardVisible = visible
        keyboardFrame = visible ? frame : nil
        logLine(visible ? "keyboard shown \(frame.map { "\(Int($0.height)) pt" } ?? "")" : "keyboard hidden")
        sendSnapshot(reason: "keyboard")
    }

    func focusChanged(_ field: String?) { focusedField = field }

    /// Puts the keyboard away: every focused SwiftUI field resigns (token) and UIKit's first
    /// responder resigns too, so a field we did not instrument cannot keep it up.
    func dismissKeyboard() {
        keyboardDismissToken += 1
        focusedField = nil
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }

    func scenePhaseChanged(_ phase: ScenePhase) {
        scenePhaseLabel = "\(phase)"
        if phase == .active {
            if audio.state == .running, !audio.isRunning { audio.restartIfNeeded(reason: "foreground") }
            sendSnapshot(reason: "foreground")
            startHeartbeat()
        } else {
            heartbeat?.invalidate()
            heartbeat = nil
            // Tell the backend the app left the foreground while the socket may still carry it;
            // afterwards it shows connected_background / offline, never live.
            sendSnapshot(reason: scenePhaseLabel)
        }
    }

    private func startHeartbeat() {
        heartbeat?.invalidate()
        heartbeat = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.sendSnapshot(reason: "heartbeat") }
        }
    }

    func makeSnapshot() -> DeviceSnapshot {
        DeviceSnapshot(
            install_id: installId,
            client_time: Date().timeIntervalSince1970,
            app_version: PortalModel.appVersion,
            build: PortalModel.appBuild,
            scene_phase: scenePhaseLabel,
            selected_tab: selectedTab,
            connection: connection.label,
            session_id: sessionId,
            mode: mode.label,
            quiet: mode.quiet,
            praying: mode.isPraying,
            audio_owner_state: PortalModel.skipPermissionPrompts ? "not started (fixture mode)" : audio.state.label,
            engine_running: audio.isRunning,
            last_buffer_age_s: audio.lastBufferAge.map { (($0 * 10).rounded()) / 10 },
            last_write_error: audio.lastWriteError,
            recognizer: recognizerAvailability.label,
            recognition_continuous: recognizer.continuous,
            recognition_listening: recognizer.isListening,
            keyboard_visible: keyboardVisible,
            keyboard_frame: keyboardFrame.map { [$0.origin.x, $0.origin.y, $0.width, $0.height].map(Double.init) },
            focused_field: focusedField,
            typed_input: typedInput,
            partial_transcript: partialTranscript,
            last_transcript: lastTranscript,
            assistant_text_tail: String(assistantText.suffix(400)),
            capture_warning: captureWarning,
            last_journal_error: lastJournalError,
            captures: captures.map { c in
                DeviceSnapshot.CaptureSummary(capture_id: c.captureId, kind: c.kind, status: c.status.label, segments: c.segments.count, bytes: c.segments.reduce(0) { $0 + $1.bytes }, pending_uploads: c.pendingUploads.count, gaps: c.gaps.count, closed: c.isClosed, expected_segments: c.expectedSegmentCount, created_at: c.createdAt.timeIntervalSince1970, last_segment_end: c.segments.compactMap(\.endedAt).max()?.timeIntervalSince1970)
            },
            recent_log: Array(log.prefix(40)),
            last_error: lastError
        )
    }

    private func snapshotDictionary() -> [String: Any] {
        guard let data = try? JSONEncoder().encode(makeSnapshot()), let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }

    /// One bounded snapshot on the socket. Nothing is sent while disconnected; the backend then shows
    /// the last snapshot's age as stale or offline instead of guessing.
    func sendSnapshot(reason: String) {
        lastSnapshotReason = reason
        guard connection.isConnected else { return }
        client.send(["type": "snapshot", "reason": reason, "snapshot": snapshotDictionary()])
    }

    private func report(_ result: CommandResult, replayed: Bool = false) {
        logLine("command \(result.commandId.prefix(8)) \(result.status.rawValue)\(replayed ? " (replayed)" : ""): \(result.detail.map { "\($0.key)=\($0.value)" }.sorted().joined(separator: " "))")
        inFlightCommands.remove(result.commandId)
        if !replayed {
            completedCommands[result.commandId] = result
            if completedCommands.count > 50 {
                for key in completedCommands.sorted(by: { $0.value.reportedAt < $1.value.reportedAt }).prefix(completedCommands.count - 50).map(\.key) { completedCommands.removeValue(forKey: key) }
            }
            store.saveCommandResults(completedCommands)
        }
        commandResultSink?(result)
        client.send(result.payload())
    }

    /// A backend command. The result is reported with the command id; `applied` only after the
    /// action ran here. Unknown kinds are `unsupported`; anything that could not be done is `failed`.
    func handleCommand(_ raw: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: raw), let command = try? JSONDecoder().decode(RemoteCommand.self, from: data) else {
            logLine("command ignored: undecodable")
            return
        }
        if let done = completedCommands[command.command_id] {
            report(done, replayed: true) // pushed again after a lost socket: answer, do not re-run
            return
        }
        if inFlightCommands.contains(command.command_id) {
            logLine("command \(command.command_id.prefix(8)) already running; duplicate push ignored")
            return
        }
        inFlightCommands.insert(command.command_id)
        guard let kind = RemoteCommandKind(rawValue: command.kind) else {
            report(CommandResult(commandId: command.command_id, status: .unsupported, detail: ["kind": command.kind]))
            return
        }
        switch kind {
        case .snapshot:
            sendSnapshot(reason: "command")
            report(CommandResult(commandId: command.command_id, status: .applied, detail: ["reason": "snapshot sent"]))
        case .dismiss_keyboard:
            Task { await dismissKeyboardObserved(commandId: command.command_id) }
        case .select_tab:
            switch RemoteCommandPlanner.selectTab(command.args) {
            case .success(let tab):
                selectedTab = tab
                report(CommandResult(commandId: command.command_id, status: .applied, detail: ["tab": tab]))
            case .failure(let error):
                report(CommandResult(commandId: command.command_id, status: .failed, detail: ["reason": error.reason]))
            }
        case .image:
            Task { await captureAndPostImage(commandId: command.command_id) }
        case .sync_manifest:
            Task { await postManifest(commandId: command.command_id) }
        case .sync_prayer_asr:
            let pending = asrJournals.values.reduce(0) { $0 + $1.unsynced.count }
            syncPrayerASR()
            report(CommandResult(commandId: command.command_id, status: .applied, detail: ["journals": "\(asrJournals.count)", "lines_sent": "\(pending)"]))
        case .copy_capture_files:
            let open = audio.openSegmentURL
            switch RemoteCommandPlanner.copyPlan(command.args, captures: captures, closedFilesOnDisk: { id in store.segmentFilesOnDisk(id, excluding: open) }) {
            case .failure(let error):
                report(CommandResult(commandId: command.command_id, status: .failed, detail: ["reason": error.reason]))
            case .success(let plan):
                Task { await copyCaptureFiles(plan, commandId: command.command_id) }
            }
        }
    }

    /// Saves the pre-state, dismisses, then reports what was actually observed afterwards (the
    /// keyboard-did-hide notification clears `keyboardVisible`), within a bounded wait.
    static let dismissObservationWindow: TimeInterval = 1.0

    private func dismissKeyboardObserved(commandId: String) async {
        let before = (visible: keyboardVisible, field: focusedField ?? "none")
        dismissKeyboard()
        var waited: TimeInterval = 0
        while keyboardVisible, waited < PortalModel.dismissObservationWindow {
            try? await Task.sleep(nanoseconds: 100_000_000)
            waited += 0.1
        }
        let detail = ["keyboard_was_visible": before.visible ? "yes" : "no", "focused_field_was": before.field, "keyboard_visible_after": keyboardVisible ? "yes" : "no", "observed_after_s": String(format: "%.1f", waited)]
        report(CommandResult(commandId: commandId, status: keyboardVisible ? .failed : .applied, detail: keyboardVisible ? detail.merging(["reason": "keyboard still visible after \(String(format: "%.1f", waited)) s"]) { a, _ in a } : detail))
    }

    /// Renders the app's own window (the foreground UI this app draws). The keyboard, system alerts
    /// and other apps are separate windows and are not in the image; the snapshot says what covers it.
    /// Requires a foreground-active scene and a successful draw; otherwise returns the reason.
    func renderAppWindow() -> Result<Data, PlanError> {
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
        guard let scene = scenes.first(where: { $0.activationState == .foregroundActive }) else {
            return .failure(PlanError(reason: "no foreground-active scene (\(scenes.map { "\($0.activationState.rawValue)" }.joined(separator: ",")))"))
        }
        guard let window = scene.windows.first(where: { $0.isKeyWindow }) ?? scene.windows.first else {
            return .failure(PlanError(reason: "foreground scene has no window"))
        }
        var drawn = false
        let renderer = UIGraphicsImageRenderer(bounds: window.bounds)
        let image = renderer.image { _ in drawn = window.drawHierarchy(in: window.bounds, afterScreenUpdates: true) }
        guard drawn else { return .failure(PlanError(reason: "drawHierarchy returned false")) }
        guard let jpeg = image.jpegData(compressionQuality: 0.6), jpeg.count > 0 else { return .failure(PlanError(reason: "jpeg encoding failed")) }
        return .success(jpeg)
    }

    private func captureAndPostImage(commandId: String) async {
        let jpeg: Data
        switch renderAppWindow() {
        case .failure(let error):
            report(CommandResult(commandId: commandId, status: .failed, detail: ["reason": error.reason]))
            return
        case .success(let data):
            jpeg = data
        }
        let digest = SHA256.hash(data: jpeg).map { String(format: "%02x", $0) }.joined()
        let snapshotB64 = (try? JSONEncoder().encode(makeSnapshot()))?.base64EncodedString() ?? ""
        do {
            let raw = try await client.postRaw("devices/\(installId)/images", body: jpeg, headers: ["Content-Type": "image/jpeg", "X-SHA256": digest, "X-Scope": "app_window", "X-Command-Id": commandId, "X-Session-Id": sessionId ?? "", "X-Client-Time": "\(Date().timeIntervalSince1970)", "X-Snapshot-B64": snapshotB64])
            let stored = (try? JSONSerialization.jsonObject(with: raw) as? [String: Any])?["stored"] as? Bool ?? false
            report(CommandResult(commandId: commandId, status: stored ? .applied : .failed, detail: ["bytes": "\(jpeg.count)", "sha256": digest, "scope": "app_window", "stored": stored ? "yes" : "no"]))
        } catch {
            report(CommandResult(commandId: commandId, status: .failed, detail: ["reason": "image upload: \(error.localizedDescription)"]))
        }
    }

    /// Manifest for recovery: every capture with its segment identities, plus closed WAVs on disk
    /// that no manifest lists (orphans). Hashing orphans runs off the main thread; the file a writer
    /// is still appending to is excluded. Nothing is moved or deleted.
    func manifestPayload() async -> [String: Any] {
        let store = self.store
        let known: [String: Set<Int>] = Dictionary(captures.map { ($0.captureId, Set($0.segments.map(\.index))) }, uniquingKeysWith: { a, _ in a })
        let open = audio.openSegmentURL
        let orphans: [[String: Any]] = await Task.detached(priority: .utility) {
            var out: [[String: Any]] = []
            for captureId in store.captureDirectoryIds() {
                for file in store.closedSegmentFiles(captureId, knownIndexes: known[captureId] ?? [], excluding: open) {
                    out.append(["capture_id": captureId, "index": file.index, "filename": file.filename, "bytes": file.bytes, "sha256": file.sha256, "in_manifest": known[captureId] != nil])
                }
            }
            return out
        }.value
        let list: [[String: Any]] = captures.map { c in
            ["capture_id": c.captureId, "kind": c.kind, "status": c.status.label, "created_at": c.createdAt.timeIntervalSince1970, "closed_at": c.closedAt?.timeIntervalSince1970 as Any, "expected_segments": c.expectedSegmentCount as Any, "finalize_requested_at": c.finalizeRequestedAt?.timeIntervalSince1970 as Any,
             "segments": c.segments.map { s in ["index": s.index, "filename": s.filename, "bytes": s.bytes, "sha256": s.sha256, "started_at": s.startedAt.timeIntervalSince1970, "ended_at": s.endedAt?.timeIntervalSince1970 as Any, "verified_uploaded": s.verifiedUploaded, "last_error": s.lastError as Any] },
             "gaps": c.gaps.map { g in ["started_at": g.startedAt.timeIntervalSince1970, "ended_at": g.endedAt?.timeIntervalSince1970 as Any, "reason": g.reason, "kind": g.kind] }]
        }
        return ["client_time": Date().timeIntervalSince1970, "install_id": installId, "captures": list, "orphans": orphans, "open_segment": open?.lastPathComponent as Any, "asr_journals": asrJournals.mapValues { ["lines": $0.lines.count, "synced_through": $0.syncedThrough] }]
    }

    private func postManifest(commandId: String) async {
        do {
            let payload = await manifestPayload()
            _ = try await client.post("devices/\(installId)/manifest", json: payload, as: [String: AnyDecodable].self)
            report(CommandResult(commandId: commandId, status: .applied, detail: ["captures": "\(captures.count)", "orphans": "\((payload["orphans"] as? [[String: Any]])?.count ?? 0)"]))
        } catch {
            report(CommandResult(commandId: commandId, status: .failed, detail: ["reason": "manifest upload: \(error.localizedDescription)"]))
        }
    }

    /// Copies the named closed capture files, bytes unchanged, to the backend's evidence store (not
    /// the prayer intake). Each file is re-hashed before sending and the backend must echo the digest
    /// and size. A file whose digest no longer matches the manifest is reported, not sent silently.
    private func copyCaptureFiles(_ plan: RemoteCommandPlanner.FileCopyPlan, commandId: String) async {
        var copied: [String] = []
        var failed: [String] = []
        let directory = store.captureDirectory(plan.captureId)
        for seg in plan.files {
            let url = directory.appendingPathComponent(seg.filename)
            // Read and hash off the main thread; bytes are sent exactly as read.
            let read: (Data, String)? = await Task.detached(priority: .utility) {
                guard let data = try? Data(contentsOf: url) else { return nil }
                return (data, SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined())
            }.value
            guard let read else { failed.append("\(seg.index):unreadable"); logLine("copy \(plan.captureId) #\(seg.index): unreadable"); continue }
            let (data, digest) = read
            do {
                let raw = try await client.postRaw("devices/\(installId)/captures/\(plan.captureId)/files/\(seg.index)", body: data, headers: ["Content-Type": "audio/wav", "X-SHA256": digest, "X-Filename": seg.filename, "X-Command-Id": commandId])
                let resp = (try? JSONSerialization.jsonObject(with: raw) as? [String: Any]) ?? [:]
                if resp["stored"] as? Bool == true, resp["sha256"] as? String == digest, resp["size_bytes"] as? Int == data.count {
                    let note = seg.manifestSha256 == nil ? ":not_in_manifest" : (seg.manifestSha256 == digest ? "" : ":digest_differs_from_manifest")
                    copied.append("\(seg.index)\(note)")
                    logLine("copy \(plan.captureId) #\(seg.index): \(data.count) bytes verified by the backend\(note)")
                } else {
                    failed.append("\(seg.index):\(resp["reason"] as? String ?? "unverified")")
                    logLine("copy \(plan.captureId) #\(seg.index): \(failed.last!)")
                }
            } catch {
                failed.append("\(seg.index):\(error.localizedDescription)")
                logLine("copy \(plan.captureId) #\(seg.index): \(error.localizedDescription)")
            }
        }
        let payload = await manifestPayload()
        _ = try? await client.post("devices/\(installId)/manifest", json: payload, as: [String: AnyDecodable].self)
        // Applied means every named file was copied and verified; anything missing or failed is a
        // failure with the exact indexes, so a partial copy can never read as complete.
        let complete = failed.isEmpty && plan.missingIndexes.isEmpty && !plan.files.isEmpty
        let detail: [String: String] = ["capture_id": plan.captureId, "in_manifest": plan.inManifest ? "yes" : "no", "copied": copied.joined(separator: ","), "failed": failed.joined(separator: ","), "missing": plan.missingIndexes.map(String.init).joined(separator: ","), "named": "\(plan.files.count + plan.missingIndexes.count)", "reason": complete ? "all named files copied and verified" : "partial: \(copied.count) of \(plan.files.count + plan.missingIndexes.count) named files verified"]
        report(CommandResult(commandId: commandId, status: complete ? .applied : .failed, detail: detail))
    }

    // MARK: uploads (stable capture id; exact identities; no duplicate captures)

    func retryPendingUploads() async {
        for capture in captures where capture.kind == "prayer" && capture.isClosed {
            await uploadPending(captureId: capture.captureId)
        }
    }

    func uploadPending(captureId: String) async {
        guard connection.isConnected, let i = captures.firstIndex(where: { $0.captureId == captureId }) else { return }
        for seg in captures[i].pendingUploads {
            let url = store.captureDirectory(captureId).appendingPathComponent(seg.filename)
            guard let data = try? Data(contentsOf: url) else { continue }
            do {
                let raw = try await client.postRaw("prayer/captures/\(captureId)/segments/\(seg.index)", body: data, headers: ["Content-Type": "audio/wav", "X-Audio-SHA256": seg.sha256, "X-Filename": seg.filename])
                let resp = try JSONDecoder().decode(SegmentUploadResponse.self, from: raw)
                if let j = captures.firstIndex(where: { $0.captureId == captureId }) {
                    if resp.status == "uploaded", resp.verified == true {
                        captures[j].recordUpload(index: seg.index, intakeSha256: resp.receipt?.audio_sha256, durable: resp.receipt?.durable, error: nil)
                    } else {
                        captures[j].recordUpload(index: seg.index, intakeSha256: nil, durable: nil, error: "\(resp.status): \(resp.reason ?? "")")
                    }
                    persistCaptures()
                }
                logLine("segment \(seg.index) upload: \(resp.status)")
            } catch {
                if let j = captures.firstIndex(where: { $0.captureId == captureId }) {
                    captures[j].recordUpload(index: seg.index, intakeSha256: nil, durable: nil, error: "upload_error: \(error.localizedDescription)")
                    persistCaptures()
                }
                logLine("segment \(seg.index) upload error: \(error.localizedDescription)")
                return
            }
        }
        await finalizeIfReady(captureId: captureId)
    }

    private func finalizeIfReady(captureId: String) async {
        guard let i = captures.firstIndex(where: { $0.captureId == captureId }), let segments = captures[i].finalizeSegments() else { return }
        if case .finalized = captures[i].status { return }
        if case .published = captures[i].status { return }
        do {
            let resp = try await client.post("prayer/captures/\(captureId)/finalize", json: ["segments": segments, "client_captured_at": captures[i].createdAt.timeIntervalSince1970], as: FinalizeResponse.self)
            if let j = captures.firstIndex(where: { $0.captureId == captureId }) {
                if resp.status == "finalized", let rid = resp.recording_id {
                    captures[j].status = .finalized(recordingId: rid)
                    captures[j].recordingId = rid
                } else if resp.status == "intake_unreachable" {
                    captures[j].status = .intakeUnreachable(reason: resp.reason ?? "")
                } else {
                    captures[j].status = .failed(reason: "\(resp.status) \(resp.reason ?? "")")
                }
                persistCaptures()
            }
            logLine("finalize \(captureId): \(resp.status) \(resp.recording_id ?? "")")
        } catch {
            logLine("finalize error: \(error.localizedDescription)")
        }
    }

    func refreshCapture(_ captureId: String) async {
        do {
            let s = try await client.get("prayer/captures/\(captureId)", as: CaptureStatusResponse.self)
            if let j = captures.firstIndex(where: { $0.captureId == captureId }) {
                switch s.status {
                case "published": captures[j].status = .published(prayerId: s.prayer_id ?? "?"); captures[j].prayerId = s.prayer_id
                case "finalized": captures[j].status = .finalized(recordingId: s.recording_id ?? "?")
                case "excluded": captures[j].status = .excluded
                case "failed": captures[j].status = .failed(reason: s.last_error ?? "")
                case "intake_unreachable": captures[j].status = .intakeUnreachable(reason: s.last_error ?? "")
                default: break
                }
                persistCaptures()
            }
        } catch { logLine("capture status error: \(error.localizedDescription)") }
    }

    // MARK: devotional

    func loadDevotional(passage: String? = nil) async {
        do {
            var q: [String: String] = [:]
            if let p = passage ?? mode.devotionalPassage { q["passage"] = p }
            let d = try await client.get("devotional", query: q, as: Devotional.self)
            devotional = d
            if let p = progress, !p.matches(passage: d.passage, revision: d.revision_hash ?? "") {
                logLine("devotional revision/passage changed; saved position no longer applies")
            }
            logLine("devotional loaded: \(d.title ?? d.passage) segments=\(d.segments?.count ?? 0) teaching=\(d.teaching_found ?? false)")
        } catch { logLine("devotional error: \(error.localizedDescription)") }
    }

    func playDevotional() {
        guard let d = devotional, let segments = d.segments, !segments.isEmpty else { return }
        guard SpeechGate.mayEnqueue(mode) else { logLine("devotional playback suppressed by gate (\(mode.label))"); return }
        let revision = d.revision_hash ?? ""
        if progress == nil || !(progress!.matches(passage: d.passage, revision: revision)) || progress!.total != segments.count {
            progress = DevotionalProgress(passage: d.passage, revision: revision, total: segments.count)
        }
        var start = progress!.resumeIndex
        if progress!.finished { start = 0; progress!.finished = false }
        devotionalPlaying = true
        for (offset, seg) in segments[start...].enumerated() {
            let prefix = seg.kind == "scripture" ? "" : "From \(seg.attribution ?? "teaching"): "
            speaker.speak(prefix + seg.text, tag: start + offset)
        }
        logLine("devotional playing from segment \(start + 1)/\(segments.count) (\(d.passage) @ \(revision.prefix(12)))")
    }

    func pauseDevotional() {
        speaker.stopAll()
        devotionalPlaying = false
        progress?.interrupted()
        persistProgress()
        logLine("devotional paused at segment \((progress?.resumeIndex ?? 0) + 1)")
    }

    private func devotionalSegmentStarted(_ index: Int) {
        progress?.started(index)
        persistProgress()
    }

    private func devotionalSegmentFinished(_ index: Int) {
        progress?.finished(index)
        if progress?.finished == true { devotionalPlaying = false }
        persistProgress()
    }

    private func persistProgress() {
        if let p = progress {
            store.saveProgress(p)
            var m = mode
            m.devotionalPosition = p.resumeIndex
            m.devotionalPassage = p.passage
            m.devotionalRevision = p.revision
            if m != mode { mode = m; store.saveMode(m) }
        }
    }

    // MARK: sources and work

    func search(_ q: String) async -> [SearchHit] {
        (try? await client.get("search", query: ["q": q], as: SearchResponse.self))?.hits ?? []
    }

    func citation(_ id: String) async -> CitationDetail? {
        try? await client.get("citations/\(id)", as: CitationDetail.self)
    }

    func refreshStatus() async {
        backendStatus = try? await client.get("status", as: BackendStatus.self)
    }

    func refreshWork() async {
        if let list = try? await client.get("work", as: WorkList.self) { work = list.operations }
    }

    /// Records the request locally first (stable operation id), then asks the service to create and
    /// dispatch it. Offline, the item stays visible as locally queued and is replayed on reconnect.
    func submitWork(kind: String, text: String) async {
        let operationId = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        localWork.insert(LocalWorkItem(operationId: operationId, kind: kind, requestText: text, createdAt: Date()), at: 0)
        store.saveLocalWork(localWork)
        logLine("work \(operationId.prefix(8)) queued locally on the phone")
        await syncLocalWork()
    }

    /// Replays pending local items: the service is asked whether the operation exists before any
    /// create, so a relaunch or reconnect never produces a second server operation.
    func syncLocalWork() async {
        guard connection.isConnected else { return }
        let pending = localWork.filter { $0.state == "pending" }
        guard !pending.isEmpty else { return }
        var existence: [String: Bool] = [:]
        for item in pending {
            do {
                _ = try await client.get("work/\(item.operationId)", as: WorkOperation.self)
                existence[item.operationId] = true
            } catch let error as PortalClient.HTTPError where error.status == 404 {
                existence[item.operationId] = false
            } catch {
                logLine("work sync: cannot check \(item.operationId.prefix(8)): \(error.localizedDescription)")
                return // unknown service state: leave everything pending
            }
        }
        for action in WorkReconciler.plan(pending: pending, serverHas: { existence[$0] ?? false }) {
            switch action {
            case .create(let id):
                guard let item = pending.first(where: { $0.operationId == id }) else { continue }
                do {
                    _ = try await client.post("work", json: ["operation_id": id, "kind": item.kind, "request_text": item.requestText], as: WorkOperation.self)
                    _ = try? await client.post("work/\(id)/dispatch", json: [:], as: [String: AnyDecodable].self)
                    settleLocalWork(id)
                } catch {
                    markLocalWork(id, error: error.localizedDescription)
                }
            case .settle(let id):
                settleLocalWork(id)
            }
        }
        await refreshWork()
    }

    private func settleLocalWork(_ id: String) {
        if let i = localWork.firstIndex(where: { $0.operationId == id }) {
            localWork[i].state = "recorded"
            localWork[i].lastError = nil
            store.saveLocalWork(localWork)
            logLine("work \(id.prefix(8)) confirmed on the service")
        }
    }

    private func markLocalWork(_ id: String, error: String) {
        if let i = localWork.firstIndex(where: { $0.operationId == id }) {
            localWork[i].lastError = error
            store.saveLocalWork(localWork)
        }
    }

    func reconcileWork(_ operationId: String) async {
        _ = try? await client.post("work/\(operationId)/reconcile", json: [:], as: [String: AnyDecodable].self)
        await refreshWork()
    }

    func dispatchWork(_ operationId: String) async {
        _ = try? await client.post("work/\(operationId)/dispatch", json: [:], as: [String: AnyDecodable].self)
        await refreshWork()
    }

    func buildSourcePacket(_ operationId: String) async {
        _ = try? await client.post("work/\(operationId)/source-packet", json: [:], as: [String: AnyDecodable].self)
        await refreshWork()
    }

    func workDetail(_ operationId: String) async -> WorkOperation? {
        try? await client.get("work/\(operationId)", as: WorkOperation.self)
    }

    func artifactText(_ operationId: String, _ artifactId: String) async -> String {
        do {
            let data = try await client.getRaw("work/\(operationId)/artifacts/\(artifactId)")
            return String(data: data, encoding: .utf8) ?? "(binary \(data.count) bytes)"
        } catch { return "Unavailable: \(error.localizedDescription)" }
    }
}

/// Tolerant decoder for JSON we only need to acknowledge.
struct AnyDecodable: Decodable {
    init(from decoder: Decoder) throws {}
}

/// JSON files under Documents: mode.json, captures.json, work-pending.jsonl, captures/<id>/NNNNNNNN.wav
final class LocalStore {
    let root: URL

    init() {
        root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: root.appendingPathComponent("captures"), withIntermediateDirectories: true)
    }

    func captureDirectory(_ captureId: String) -> URL { root.appendingPathComponent("captures").appendingPathComponent(captureId) }

    func loadMode() -> PortalMode? {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("mode.json")) else { return nil }
        return try? JSONDecoder().decode(PortalMode.self, from: data)
    }

    func saveMode(_ mode: PortalMode) {
        if let data = try? JSONEncoder().encode(mode) { try? data.write(to: root.appendingPathComponent("mode.json"), options: .atomic) }
    }

    func loadCaptures() -> [CaptureManifest] {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("captures.json")) else { return [] }
        return (try? JSONDecoder().decode([CaptureManifest].self, from: data)) ?? []
    }

    func saveCaptures(_ captures: [CaptureManifest]) {
        if let data = try? JSONEncoder().encode(captures) { try? data.write(to: root.appendingPathComponent("captures.json"), options: .atomic) }
    }

    func loadLocalWork() -> [LocalWorkItem] {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("work-local.json")) else { return [] }
        return (try? JSONDecoder().decode([LocalWorkItem].self, from: data)) ?? []
    }

    func saveLocalWork(_ items: [LocalWorkItem]) {
        if let data = try? JSONEncoder().encode(items) { try? data.write(to: root.appendingPathComponent("work-local.json"), options: .atomic) }
    }

    func loadProgress() -> DevotionalProgress? {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("devotional-progress.json")) else { return nil }
        return try? JSONDecoder().decode(DevotionalProgress.self, from: data)
    }

    func saveProgress(_ progress: DevotionalProgress) {
        if let data = try? JSONEncoder().encode(progress) { try? data.write(to: root.appendingPathComponent("devotional-progress.json"), options: .atomic) }
    }

    /// Capture directories present on disk (manifest-listed or not).
    func captureDirectoryIds() -> [String] {
        ((try? FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent("captures").path)) ?? []).sorted()
    }

    /// index -> filename of segment files on disk for a capture, excluding the file still being
    /// written. No hashing (cheap enough for the main thread).
    func segmentFilesOnDisk(_ captureId: String, excluding open: URL?) -> [Int: String] {
        let dir = captureDirectory(captureId)
        var out: [Int: String] = [:]
        for name in (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? [] where name.hasSuffix(".wav") {
            guard let index = Int(name.dropLast(4)) else { continue }
            if let open, open.standardizedFileURL == dir.appendingPathComponent(name).standardizedFileURL { continue }
            out[index] = name
        }
        return out
    }

    // Durable recent log: Documents/recent-log.txt, appended on a serial queue, bounded by rotation.
    private static let logQueue = DispatchQueue(label: "rlmadk.log", qos: .utility)
    private var logURL: URL { root.appendingPathComponent("recent-log.txt") }

    func appendLog(_ line: String) {
        let url = logURL
        LocalStore.logQueue.async {
            let data = Data((line + "\n").utf8)
            if let handle = try? FileHandle(forWritingTo: url) {
                _ = try? handle.seekToEnd()
                try? handle.write(contentsOf: data)
                try? handle.close()
            } else {
                try? data.write(to: url, options: .atomic)
            }
            if let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int), size > 512_000,
               let text = try? String(contentsOf: url, encoding: .utf8) {
                let kept = text.split(separator: "\n").suffix(1000).joined(separator: "\n") + "\n"
                try? Data(kept.utf8).write(to: url, options: .atomic)
            }
        }
    }

    /// Last lines from the durable log, newest first, for the in-memory log on launch.
    func loadRecentLog(limit: Int = 200) -> [String] {
        LocalStore.logQueue.sync {} // any pending append from a previous model lands first
        guard let text = try? String(contentsOf: logURL, encoding: .utf8) else { return [] }
        return Array(text.split(separator: "\n").suffix(limit).reversed().map(String.init))
    }

    func loadCommandResults() -> [String: CommandResult] {
        guard let data = try? Data(contentsOf: root.appendingPathComponent("command-results.json")) else { return [:] }
        return (try? JSONDecoder().decode([String: CommandResult].self, from: data)) ?? [:]
    }

    func saveCommandResults(_ results: [String: CommandResult]) {
        if let data = try? JSONEncoder().encode(results) { try? data.write(to: root.appendingPathComponent("command-results.json"), options: .atomic) }
    }

    // Prayer ASR journal: Documents/prayer-asr/<captureId>.jsonl (append-only) and prayer-asr-sync.json.
    private var asrDirectory: URL { root.appendingPathComponent("prayer-asr") }

    /// Appends one journal line and flushes it; any failure is thrown to the caller (shown, logged).
    func appendASR(captureId: String, line: PrayerASRJournal.Line) throws {
        try FileManager.default.createDirectory(at: asrDirectory, withIntermediateDirectories: true)
        var data = try JSONEncoder().encode(line)
        data.append(0x0A)
        let url = asrDirectory.appendingPathComponent("\(captureId).jsonl")
        if FileManager.default.fileExists(atPath: url.path) {
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
            try handle.synchronize()
        } else {
            try data.write(to: url, options: .atomic)
        }
    }

    func loadASRJournals() -> [String: PrayerASRJournal] {
        let synced = (try? JSONDecoder().decode([String: Int].self, from: Data(contentsOf: root.appendingPathComponent("prayer-asr-sync.json")))) ?? [:]
        var out: [String: PrayerASRJournal] = [:]
        for name in (try? FileManager.default.contentsOfDirectory(atPath: asrDirectory.path)) ?? [] where name.hasSuffix(".jsonl") {
            let captureId = String(name.dropLast(6))
            guard let text = try? String(contentsOf: asrDirectory.appendingPathComponent(name), encoding: .utf8) else { continue }
            let lines = text.split(separator: "\n").compactMap { try? JSONDecoder().decode(PrayerASRJournal.Line.self, from: Data($0.utf8)) }
            out[captureId] = PrayerASRJournal(captureId: captureId, lines: lines, syncedThrough: synced[captureId] ?? 0)
        }
        return out
    }

    func saveASRSync(_ journals: [String: PrayerASRJournal]) {
        let map = journals.mapValues { $0.syncedThrough }
        if let data = try? JSONEncoder().encode(map) { try? data.write(to: root.appendingPathComponent("prayer-asr-sync.json"), options: .atomic) }
    }

    /// Closed segment files on disk for a capture that the manifest does not list yet. `excluding`
    /// is the file a writer is still appending to: it is neither closed nor complete and is skipped.
    func closedSegmentFiles(_ captureId: String, knownIndexes: Set<Int>, excluding open: URL?) -> [(index: Int, filename: String, bytes: Int, sha256: String)] {
        let dir = captureDirectory(captureId)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        var out: [(index: Int, filename: String, bytes: Int, sha256: String)] = []
        for name in names.sorted() where name.hasSuffix(".wav") {
            guard let index = Int(name.dropLast(4)), !knownIndexes.contains(index) else { continue }
            if let open, open.standardizedFileURL == dir.appendingPathComponent(name).standardizedFileURL { continue }
            guard let data = try? Data(contentsOf: dir.appendingPathComponent(name)), !data.isEmpty else { continue }
            let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            out.append((index, name, data.count, digest))
        }
        return out
    }
}
