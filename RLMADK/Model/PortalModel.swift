import AVFoundation
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
    private var lastSpeechAt = Date()
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
        let rec = Recognizer()
        self.store = store
        mode = loadedMode
        captures = loadedCaptures
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
        audio.onStateChange = { [weak self] s in Task { @MainActor in self?.audioState = s } }
        audio.onBuffer = { [weak self] buffer, _ in self?.recognizer.append(buffer) }
        audio.onGap = { [weak self] gap in Task { @MainActor in self?.recordGap(gap) } }
        audio.onSegmentClosed = { [weak self] captureId, seg in Task { @MainActor in self?.segmentClosed(captureId: captureId, seg) } }
        recognizer.onAvailability = { [weak self] a in Task { @MainActor in self?.recognizerAvailability = a } }
        recognizer.onPartial = { [weak self] text in Task { @MainActor in self?.partial(text) } }
        recognizer.onFinal = { [weak self] text in Task { @MainActor in self?.final(text, source: "voice") } }
        recognizer.onGap = { [weak self] gap in Task { @MainActor in self?.recordGap(gap) } }
        speaker.onStateChange = { [weak self] s in Task { @MainActor in self?.speaking = s } }
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

    func startAll() {
        recognizer.requestAuthorization { [weak self] _ in Task { @MainActor in self?.startAudio() } }
        AVAudioSession.sharedInstance().requestRecordPermission { [weak self] _ in Task { @MainActor in self?.startAudio() } }
        client.connect()
        Task { await refreshStatus() }
    }

    private func startAudio() {
        guard audioState != .running else { return }
        let captureId = mode.prayerCaptureId ?? ambientCaptureId
        do {
            try audio.start(captureId: captureId, directory: store.captureDirectory(captureId))
            logLine("audio owner running; capture \(captureId)")
        } catch {
            logLine("audio owner failed: \(error.localizedDescription)")
        }
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
        recognizer.start()
        lastSpeechAt = Date()
        startSilenceTimer()
        logLine(recognizerAvailability.usable ? "listening (on-device)" : "listen requested but \(recognizerAvailability.label)")
    }

    private func partial(_ text: String) {
        partialTranscript = text
        lastSpeechAt = Date()
        if case .responding = mode.activity { apply(.userSpeechStarted) } // barge-in on real speech
        if let turnId = mode.currentTurnId { client.send(["type": "partial_transcript", "turn_id": turnId, "text": text]) }
    }

    private func final(_ text: String, source: String) {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }
        partialTranscript = ""
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
                let silent = Date().timeIntervalSince(self.lastSpeechAt)
                self.apply(.silence(seconds: silent)) // no-op by design; prayer never ends on silence
                if !self.mode.isPraying, self.recognizer.isListening, silent > 2.5 { self.recognizer.endUtterance() }
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
        recognizer.stop()
        if audioState == .running { audio.beginCapture(captureId: captureId, directory: store.captureDirectory(captureId)) }
        logLine("prayer begun; capture \(captureId)")
    }

    func endPrayer() {
        guard mode.isPraying else { return }
        apply(.endPrayer)
        logLine("prayer ended explicitly")
    }

    private func finishCapture(_ captureId: String) {
        guard let i = captures.firstIndex(where: { $0.captureId == captureId }) else { return }
        captures[i].closedAt = Date()
        captures[i].finalizeRequestedAt = Date()
        persistCaptures()
        if audioState == .running {
            audio.rotateSegment() // closes the final prayer segment; segmentClosed() will upload it
            audio.beginCapture(captureId: ambientCaptureId, directory: store.captureDirectory(ambientCaptureId))
        }
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
            if mode.devotionalPassage != d.passage {
                var m = mode
                m.devotionalPassage = d.passage
                m.devotionalPosition = 0
                mode = m
                store.saveMode(m)
            }
            logLine("devotional loaded: \(d.title ?? d.passage) segments=\(d.segments?.count ?? 0) teaching=\(d.teaching_found ?? false)")
        } catch { logLine("devotional error: \(error.localizedDescription)") }
    }

    func playDevotional() {
        guard let d = devotional, let segments = d.segments, !segments.isEmpty else { return }
        guard SpeechGate.mayEnqueue(mode) else { logLine("devotional playback suppressed by gate (\(mode.label))"); return }
        devotionalPlaying = true
        let start = min(mode.devotionalPosition, segments.count - 1)
        for (offset, seg) in segments[start...].enumerated() {
            let prefix = seg.kind == "scripture" ? "" : "From \(seg.attribution ?? "teaching"): "
            speaker.speak(prefix + seg.text)
            // Position advances as utterances are enqueued; interruption keeps the last reached index.
            var m = mode
            m.devotionalPosition = start + offset
            mode = m
        }
        store.saveMode(mode)
    }

    func pauseDevotional() {
        speaker.stopAll()
        devotionalPlaying = false
        store.saveMode(mode)
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

    func submitWork(kind: String, text: String) async {
        let operationId = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        store.appendPendingWork(operationId: operationId, kind: kind, text: text) // local record first
        do {
            _ = try await client.post("work", json: ["operation_id": operationId, "kind": kind, "request_text": text], as: WorkOperation.self)
            _ = try await client.post("work/\(operationId)/dispatch", json: [:], as: [String: AnyDecodable].self)
            logLine("work \(operationId.prefix(8)) recorded and dispatched")
        } catch { logLine("work submit error: \(error.localizedDescription)") }
        await refreshWork()
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

    func appendPendingWork(operationId: String, kind: String, text: String) {
        let line = (try? JSONSerialization.data(withJSONObject: ["operation_id": operationId, "kind": kind, "request_text": text, "created_at": Date().timeIntervalSince1970])) ?? Data()
        let url = root.appendingPathComponent("work-pending.jsonl")
        if let handle = try? FileHandle(forWritingTo: url) {
            handle.seekToEndOfFile()
            handle.write(line + Data("\n".utf8))
            try? handle.close()
        } else {
            try? (line + Data("\n".utf8)).write(to: url)
        }
    }
}
