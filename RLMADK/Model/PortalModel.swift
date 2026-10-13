import AVFoundation
import CryptoKit
import Foundation
import SwiftUI

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
            let found = store.closedSegmentFiles(loadedCaptures[i].captureId, knownIndexes: Set(loadedCaptures[i].segments.map(\.index)))
            _ = loadedCaptures[i].reconcileFiles(found)
        }
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
        if envURL != nil { backendURLString = urlString }
        store.saveCaptures(captures)
        wire()
        // Relaunch: mode (including quiet and an open prayer) is restored, never reset.
        apply(.relaunch)
        logLine("launched; mode=\(mode.label) quiet=\(mode.quiet) prayer=\(mode.prayerCaptureId ?? "-")")
    }

    // MARK: wiring

    private func wire() {
        audio.onStateChange = { [weak self] s in Task { @MainActor in self?.audioStateChanged(s) } }
        audio.onBuffer = { [weak self] buffer, _ in self?.recognizer.append(buffer) }
        audio.onGap = { [weak self] gap in Task { @MainActor in self?.recordGap(gap) } }
        audio.onSegmentClosed = { [weak self] captureId, seg in Task { @MainActor in self?.segmentClosed(captureId: captureId, seg) } }
        recognizer.onAvailability = { [weak self] a in Task { @MainActor in self?.availabilityChanged(a) } }
        recognizer.onPartial = { [weak self] text in Task { @MainActor in self?.partial(text) } }
        recognizer.onFinal = { [weak self] text in Task { @MainActor in self?.final(text, source: "voice") } }
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
        log.insert("\(stamp) \(s)", at: 0)
        if log.count > 200 { log.removeLast() }
    }

    private func apply(_ event: PortalEvent) {
        let (next, effects) = PortalReducer.apply(event, to: mode)
        if next != mode {
            mode = next
            store.saveMode(mode)
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
            logLine("prayer saved; waiting for the user")
        }
    }

    // MARK: lifecycle

    /// CI fixture/demo runs set `RLMADK_SKIP_PERMISSION_PROMPTS=1`: neither speech recognition nor the
    /// microphone is requested and the audio owner is not started (the simulator's microphone prompt
    /// cannot be answered unattended), so screenshots show the UI with the audio owner idle and the
    /// exact "live voice unavailable" condition. Real devices always prompt and start the owner.
    static let skipPermissionPrompts = ProcessInfo.processInfo.environment["RLMADK_SKIP_PERMISSION_PROMPTS"] == "1"

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
            client.send(["type": "hello", "mode": ["quiet": mode.quiet, "activity": mode.label]])
            apply(.connectionRestored)
            logLine("connected session \(id.prefix(8)); mode stays \(mode.label)")
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

    /// Test seam for the partial path (the recognizer is not available in the simulator).
    func partialTranscriptReceived(_ text: String) { partial(text) }
    var endpointClock: EndpointClock { endpoint }

    private func final(_ text: String, source: String) {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }
        partialTranscript = ""
        utteranceInterrupted = false
        lastTranscript = cleaned
        logLine("final (\(source)): \(cleaned)")
        if handleVoiceCommand(cleaned) { return }
        if mode.isPraying { return } // prayer: transcripts are captured locally, never sent as turns
        let turnId = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        apply(.finalTranscript(turnId: turnId))
    }

    /// Typed input is labelled as typed; it exercises the same turn path but is not proof of live voice.
    func submitTyped() {
        let text = typedInput
        typedInput = ""
        final(text, source: "typed")
    }

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
        apply(.enterPrayer(captureId: captureId))
        if audioState == .running { audio.beginCapture(captureId: captureId, directory: store.captureDirectory(captureId), startIndex: 0) }
        logLine("prayer begun; capture \(captureId); recognition continues for end-of-prayer commands only")
    }

    func endPrayer() {
        guard mode.isPraying else { return }
        apply(.endPrayer)
        logLine("prayer ended explicitly")
    }

    private func finishCapture(_ captureId: String) {
        guard let i = captures.firstIndex(where: { $0.captureId == captureId }) else { return }
        captures[i].finalizeRequestedAt = Date()
        persistCaptures()
        let ambientStart = captures.first(where: { $0.captureId == ambientCaptureId })?.nextSegmentIndex ?? 0
        if audioState == .running {
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
        captures[i].expectedSegmentCount = produced
        captures[i].closedAt = Date()
        persistCaptures()
        logLine("capture \(captureId) settled: \(produced) segments expected, \(captures[i].segments.count) recorded")
        Task { await self.uploadPending(captureId: captureId) }
    }

    private func segmentClosed(captureId: String, _ seg: SegmentRecord) {
        guard let i = captures.firstIndex(where: { $0.captureId == captureId }) else { return }
        if !captures[i].segments.contains(where: { $0.index == seg.index }) { captures[i].segments.append(seg) }
        persistCaptures()
        logLine("segment \(seg.index) closed for \(captureId): \(seg.bytes) bytes sha \(seg.sha256.prefix(10))")
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

    /// Closed segment files on disk for a capture that the manifest does not list yet.
    func closedSegmentFiles(_ captureId: String, knownIndexes: Set<Int>) -> [(index: Int, filename: String, bytes: Int, sha256: String)] {
        let dir = captureDirectory(captureId)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        var out: [(index: Int, filename: String, bytes: Int, sha256: String)] = []
        for name in names.sorted() where name.hasSuffix(".wav") {
            guard let index = Int(name.dropLast(4)), !knownIndexes.contains(index) else { continue }
            guard let data = try? Data(contentsOf: dir.appendingPathComponent(name)), !data.isEmpty else { continue }
            let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            out.append((index, name, data.count, digest))
        }
        return out
    }
}
