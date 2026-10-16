import AVFoundation
import CryptoKit
import Foundation
import SwiftUI
import UIKit

/// Orchestrates the single audio owner, on-device recognition, the talk tier (Pipecat, the app's
/// voice), the backend's text/control WebSocket (inspection, prayer journal sync), locally
/// persisted captures and the durable work list. Mode is persisted independently of any
/// connection or session id, and the phone's mode is the authority the talk server follows.
@MainActor
final class PortalModel: ObservableObject {
    // Persisted
    @Published private(set) var mode: PortalMode
    @Published private(set) var captures: [CaptureManifest]
    @AppStorage("backendURL") var backendURLString: String = PortalModel.defaultBackendURL
    @AppStorage("talkURL") var talkURLString: String = PortalModel.defaultTalkURL
    @AppStorage("talkTransport") var talkTransportRaw: String = PortalModel.defaultTalkTransport.rawValue

    // Live
    @Published var connection: PortalClient.Connection = .disconnected(nil)
    @Published var audioState: AudioOwner.State = .idle
    @Published var recognizerAvailability: Recognizer.Availability
    @Published var partialTranscript: String = ""
    @Published var lastTranscript: String = ""
    /// The bot's reply text for the current exchange (talk tier `bot-transcription`).
    @Published var assistantText: String = ""
    /// What the talk server heard (its own transcript of the last utterance).
    @Published var serverHeard: String = ""
    @Published var devotional: Devotional?
    @Published var devotionalPlaying = false
    @Published var work: [WorkOperation] = []
    @Published private(set) var localWork: [LocalWorkItem] = []
    @Published private(set) var progress: DevotionalProgress?
    @Published var backendStatus: BackendStatus?
    @Published var log: [String] = []
    /// The talk tier reports bot speech (between bot-started-speaking and bot-stopped-speaking).
    @Published var botSpeaking = false
    /// Bot audio is actually audible on the phone (the voice output's own view).
    @Published var botAudible = false
    @Published var typedInput: String = ""
    @Published var lastError: String?

    // Scripture pane
    /// The passage open in the pane (the talk tier's `passage` events are its authority).
    @Published private(set) var passage: ScripturePassage?
    /// Reading progress of that passage (verse heard, verse playing, word shading).
    @Published private(set) var reading = ReadingProgress()
    /// A passage requested from the pane and not yet answered by the server.
    @Published private(set) var passageRequested: String?
    /// A newer private build to offer (the update banner; AppUpdate.swift), or nil.
    @Published private(set) var updateOffer: LatestBuild?
    /// Where latest.json is read from: `RLMADK_UPDATE_URL`, else the private install page.
    let updateURLString = ProcessInfo.processInfo.environment["RLMADK_UPDATE_URL"] ?? AppUpdate.defaultURL
    /// Test seams: how latest.json is fetched and how an install link is opened.
    var updateFetcher: (URL) async throws -> Data = { url in
        let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10)
        let (data, response) = try await URLSession.shared.data(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
        return data
    }
    var urlOpener: @MainActor (URL) async -> Bool = { await UIApplication.shared.open($0) }
    /// Who answers, as the talk server names it; labels replies in the conversation ("Qwen3.8: ...").
    @Published private(set) var assistantName: String = UserDefaults.standard.string(forKey: "assistantName") ?? "Assistant"
    /// The banner's install hand-off in progress (UpdateInstall in AppUpdate.swift), or nil.
    @Published private(set) var updateInstall: UpdateInstall?
    /// Shown on the first launch of a newer build ("Updated to build 21 (from 20)"), with its notes.
    @Published private(set) var updatedNotice: String?
    @Published private(set) var updatedNotes: String?
    /// Test seams: how the app steps to the Home Screen (false when it cannot), how long it waits
    /// for Apple's prompt, and the pause that lets the banner show "Installing" before it leaves.
    var appSuspender: @MainActor () -> Bool = { PortalModel.suspendToHomeScreen() }
    var updatePromptTimeout: TimeInterval = 4
    var updateLeaveDelay: TimeInterval = 0.4
    private var updateTimer: Timer?
    private var lastUpdateCheckError: String?
    /// The last update check's outcome, shown in Diagnostics and the snapshot: when it ran, the
    /// published build and whether that is newer, or why the check failed.
    @Published private(set) var updateCheckStatus: String = "not checked yet"
    /// The Bug chip's report (BugReport.swift); while it is open the phone keeps bot speech silent.
    @Published private(set) var bug = BugReportState() {
        didSet { if bug.silencesOutput != oldValue.silencesOutput { talk?.voice.gateOpen = speechGateOpen } }
    }
    @Published var scriptureFontSize: Double = UserDefaults.standard.object(forKey: "scriptureFontSize") as? Double ?? 21 {
        didSet { UserDefaults.standard.set(scriptureFontSize, forKey: "scriptureFontSize") }
    }

    // Talk tier
    @Published private(set) var talkState: TalkConnectionState = .disconnected(nil)
    /// Transport in use now; differs from the selection after a coexistence fallback.
    @Published private(set) var activeTalkTransport: TalkTransportKind?
    @Published private(set) var policy: ConversationPolicy
    @Published private(set) var policySource: String
    /// WebRTC coexistence spike: the latest observation (see ios-client.md).
    @Published private(set) var webrtcSpike: String = "not run"
    /// Set when the WebRTC transport stopped the audio owner's capture on this build; the app then
    /// runs the WebSocket transport until WebRTC is selected again in Diagnostics.
    @Published private(set) var webrtcFallbackReason: String?
    private(set) var talk: TalkClient?
    /// Test seam: builds the talk client for a transport (fakes in controller tests).
    var talkFactory: ((TalkTransportKind) -> TalkClient)?
    /// Last floor mode told to the talk server (Diagnostics, tests).
    private(set) var lastClientModeSent: ClientFloorMode?
    private var modeReason: String?
    private var floor: PrayerFloor
    private var floorTimer: Timer?
    /// Test seam: the clock the prayer floor reads.
    var floorClock: () -> Date = { Date() }
    /// Test seam: replaces the audio owner's voice-level facts (nil = none available).
    var voiceProbe: (() -> VoiceFacts?)?
    /// Prayer began from an address the recognizer could not adopt: the conversation final that
    /// carries the address words still belongs to the prayer.
    private var addressFinalPending = false
    private var devotionalTracker: DevotionalTracker?
    private var captureWatchdog: Timer?
    private var watchdogRestarts = 0
    private var spikeTimer: Timer?
    private var spikeConnectedAt: Date?
    private var spikeBaselineBytes: Int?
    private var spikeBaselineFile: String?
    private var spikeBaselineSegments = 0
    private var spikeNotDeliveringSince: Date?
    private var spikeRestartTried = false
    private var spikeSuccessRecorded = false
    private var talkLoggedState: String?
    private var botTurnCounter = 0

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
    /// Server-side prayer transcript lines (`prayer_text`) journaled per capture, by count.
    private(set) var talkPrayerLines: [String: Int] = [:]
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
    private(set) var client: PortalClient
    private var ambientCaptureId: String
    private let store: LocalStore
    private var ciDemoDone = false
    private var silenceTimer: Timer?
    private var endpoint = EndpointClock()
    private var sessionId: String?
    // MainActor reentrancy permits another callback while HTTP is awaiting. One owner per
    // capture covers both upload and finalize; callbacks during that work request another pass.
    private var uploadingCaptures: Set<String> = []
    private var uploadRequested: Set<String> = []

    static let defaultBackendURL = ProcessInfo.processInfo.environment["RLMADK_BACKEND_URL"] ?? "http://100.90.105.30:8765"
    /// The Pipecat talk tier on Spark-A (both transports on one port).
    static let defaultTalkURL = ProcessInfo.processInfo.environment["RLMADK_TALK_URL"] ?? "http://100.121.221.42:7860"
    static let defaultTalkTransport = TalkTransportKind(rawValue: ProcessInfo.processInfo.environment["RLMADK_TALK_TRANSPORT"] ?? "") ?? .webrtc

    init(store: LocalStore = LocalStore(), client suppliedClient: PortalClient? = nil) {
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
        let cachedPolicy = PolicyStore.load(root: store.root)
        self.store = store
        mode = loadedMode
        captures = loadedCaptures
        localWork = store.loadLocalWork()
        progress = store.loadProgress()
        recognizer = rec
        recognizerAvailability = rec.availability
        client = suppliedClient ?? PortalClient(baseURL: URL(string: urlString) ?? URL(string: PortalModel.defaultBackendURL)!)
        ambientCaptureId = ambient
        installId = PortalModel.loadOrCreateInstallId()
        policy = cachedPolicy.policy
        policySource = cachedPolicy.source
        floor = PrayerFloor(policy: cachedPolicy.policy)
        webrtcFallbackReason = UserDefaults.standard.string(forKey: "webrtcFallback-\(PortalModel.appBuild)")
        if envURL != nil { backendURLString = urlString }
        if let envTalk = ProcessInfo.processInfo.environment["RLMADK_TALK_URL"] { talkURLString = envTalk }
        if let envTransport = ProcessInfo.processInfo.environment["RLMADK_TALK_TRANSPORT"], TalkTransportKind(rawValue: envTransport) != nil { talkTransportRaw = envTransport }
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
            startFloorTimer()
        }
        logLine("launched; mode=\(mode.label) quiet=\(mode.quiet) prayer=\(mode.prayerCaptureId ?? "-") talk=\(talkTransportRaw) \(talkURLString) policy=\(policySource)")
        let lastLaunchedBuild = UserDefaults.standard.object(forKey: "lastLaunchedBuild") as? Int
        if let notice = AppUpdate.updatedNotice(lastLaunchedBuild: lastLaunchedBuild, currentBuild: PortalModel.appBuild) {
            updatedNotice = notice
            logLine(notice)
        }
        if let current = Int(PortalModel.appBuild) { UserDefaults.standard.set(current, forKey: "lastLaunchedBuild") }
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
            // The gate is evaluated on every mode change, before any effect runs.
            talk?.voice.gateOpen = speechGateOpen
            if modeChanged { sendSnapshot(reason: "mode") }
        }
        for effect in effects { perform(effect) }
        modeReason = nil
    }

    private func perform(_ effect: PortalEffect) {
        switch effect {
        case .stopVoice:
            talk?.voice.stop()
            devotionalInterrupted(reason: "output stopped")
        case .sendClientMode(let floorMode):
            sendClientMode(floorMode, reason: modeReason ?? "phone")
        case .savePrayer(let captureId):
            finishCapture(captureId)
        case .waitForUser:
            logLine("prayer ended; capture settling (saved only once the writer reports its segments); waiting for the user")
        }
    }

    /// The phone's speech gate: the mode's rule (quiet, prayer) and an open bug report.
    private var speechGateOpen: Bool { SpeechGate.mayEnqueue(mode) && !bug.silencesOutput }

    private func sendClientMode(_ floorMode: ClientFloorMode, reason: String) {
        lastClientModeSent = floorMode
        talk?.send(.clientMode(floorMode, reason: reason))
    }

    // MARK: lifecycle

    /// CI fixture/demo runs set `RLMADK_SKIP_PERMISSION_PROMPTS=1`: neither speech recognition nor the
    /// microphone is requested and the audio owner is not started (the simulator's microphone prompt
    /// cannot be answered unattended), so screenshots show the UI with the audio owner idle and the
    /// exact "live voice unavailable" condition. The talk tier then runs the WebSocket transport
    /// (silence uplink), never WebRTC, which would open the microphone itself. Real devices always
    /// prompt and start the owner.
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
        Task { await refreshPolicy() }
        startCaptureWatchdog()
        connectTalk()
    }

    private func startAudio() {
        // The owner's own state is authoritative; the published mirror is updated through a queued Task.
        guard audio.state != .running else { reconcileRecognition(); return }
        let captureId = mode.prayerCaptureId ?? ambientCaptureId
        let startIndex = captures.first(where: { $0.captureId == captureId })?.nextSegmentIndex ?? 0
        do {
            try audio.start(captureId: captureId, directory: store.captureDirectory(captureId), startIndex: startIndex)
            logLine("audio owner running; capture \(captureId) from segment \(startIndex); \(audio.sessionDescription)")
        } catch {
            logLine("audio owner failed: \(error.localizedDescription)")
        }
        reconcileRecognition()
    }

    /// Every two seconds: an owner that intends to run but whose engine stopped (another audio
    /// client, a deactivation we were not told about) is restarted, and the restart is logged.
    private func startCaptureWatchdog() {
        captureWatchdog?.invalidate()
        captureWatchdog = Timer.scheduledTimer(withTimeInterval: 2.0, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.audio.state == .running, !self.audio.isRunning else { return }
                self.watchdogRestarts += 1
                let restarted = self.audio.restartIfNeeded(reason: "watchdog")
                self.logLine("capture watchdog: engine was stopped while recording; restart \(restarted ? "ok" : "pending/failed") (#\(self.watchdogRestarts)); \(self.audio.sessionDescription)")
                self.sendSnapshot(reason: "capture")
            }
        }
    }

    /// `GET /policy` from the skill service; the cached policy stays when it has none.
    func refreshPolicy() async {
        guard let fetched = await PolicyStore.fetch(client: client, root: store.root) else {
            logLine("policy: none from \(backendURLString)/policy; using \(policySource)")
            return
        }
        applyPolicy(fetched.policy, source: fetched.source)
    }

    /// Test seam and the fetch path: installs a policy for every floor rule.
    func applyPolicy(_ newPolicy: ConversationPolicy, source: String) {
        policy = newPolicy
        policySource = source
        floor.setPolicy(newPolicy)
        logLine("policy \(source): \(newPolicy.addressPhrases.count) address, \(newPolicy.amenPhrases.count) amen phrases, amen window \(newPolicy.amenSilenceSeconds) s")
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
            }
        } else if was {
            apply(.connectionLost)
            logLine("connection lost; recording continues; mode stays \(mode.label)")
        }
    }

    // MARK: transcripts (on-device): prayer floor, control phrases, prayer journal

    /// The Listen button: restarts on-device recognition now (the talk server hears the audio on
    /// its own; this is the phone's recognizer for floor rules and control phrases).
    func beginListening() {
        guard !mode.isPraying else { return }
        partialTranscript = ""
        if recognizer.continuous { recognizer.start() } else { recognizer.startContinuous() }
        endpoint.requestStarted()
        floor.newUtterance()
        startSilenceTimer()
        logLine(recognizerAvailability.usable ? "listening (on-device, manual restart)" : "listen requested but \(recognizerAvailability.label)")
    }

    /// Recognition request restarted (after a final, no-speech or error): the silence window restarts
    /// too, so the fresh request is not end-pointed immediately by the previous utterance's age.
    func recognitionRestarted(_ reason: String) {
        endpoint.requestStarted()
        floor.newUtterance()
        logLine("recognition restarted (\(reason))")
    }

    private func partial(_ text: String) {
        partialTranscript = text
        endpoint.speech()
        // Reporting a bug: words are the report's; no address or phrase acts on them.
        if bug.silencesOutput { return }
        act(on: floor.observe(text: text, final: false, praying: mode.isPraying, at: floorClock()), text: text)
        trackPrayerPartial(text)
    }

    /// The latest on-device partial of the open prayer. On iOS 26 the on-device recognizer starts
    /// its partial text afresh after a pause inside one request and may never deliver a final for
    /// it (device run 2026-10-14: a three-sentence prayer journaled nothing). A partial that does
    /// not continue the previous one closes that utterance, which is journaled then; the last one
    /// is journaled when the prayer ends. A later final is still journaled as it arrives, so a line
    /// can repeat; the journal is provisional text, the intake transcribes the audio.
    private var prayerPartial: (captureId: String, text: String)?

    private func trackPrayerPartial(_ text: String) {
        guard let captureId = mode.prayerCaptureId else { return }
        if let previous = prayerPartial, previous.captureId == captureId, !PortalModel.continues(previous.text, with: text) {
            journalPrayerASR(captureId: captureId, text: previous.text)
            logLine("prayer partial closed by a pause; journaled (\(previous.text.prefix(40)))")
        }
        prayerPartial = (captureId, text)
    }

    /// True when `next` extends `previous` (its leading words, allowing the last two to be revised).
    static func continues(_ previous: String, with next: String) -> Bool {
        let a = FloorText.words(previous), b = FloorText.words(next)
        guard !a.isEmpty else { return true }
        let k = max(1, a.count - 2)
        return b.count >= k && Array(b.prefix(k)) == Array(a.prefix(k))
    }

    /// Test seams for the recognizer paths (the recognizer is not available in the simulator).
    func partialTranscriptReceived(_ text: String) { partial(text) }
    /// `owner` is the capture id the recognition request was opened under (what the recognizer
    /// reports with every final); nil means a conversation request.
    func finalTranscriptReceived(_ text: String, owner: String? = nil) { final(text, source: "voice", owner: owner) }
    var endpointClock: EndpointClock { endpoint }
    var prayerFloor: PrayerFloor { floor }

    private func act(on decision: PrayerFloor.Decision, text: String) {
        switch decision {
        case .none:
            break
        case .enterPrayer(let address):
            beginPrayer(trigger: .address(address))
        case .amenPending:
            let voice: VoiceFacts? = voiceProbe.map { $0() } ?? (audio.state == .running ? audio.voiceFacts : nil)
            let voiceNote = voice.map { v in "voice \(v.active ? "active" : "quiet"), ended \(v.lastVoiceEnd.map { String(format: "%.2f s ago", floorClock().timeIntervalSince($0)) } ?? "never")" } ?? "no level facts"
            logLine("amen heard (\"\(text.suffix(60))\"); prayer ends after \(policy.amenSilenceSeconds) s with no new words (\(voiceNote))")
        case .amenCancelled:
            logLine("more words after amen; prayer continues")
        case .endPrayer:
            endPrayer(reason: "amen")
        }
    }

    private func final(_ text: String, source: String, owner: String? = nil) {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }
        partialTranscript = ""
        lastTranscript = cleaned
        logLine("final (\(source)\(owner.map { ", prayer \($0)" } ?? "")): \(cleaned)")
        if let owner {
            // A request opened under (or adopted by) a prayer capture: its text belongs to that
            // capture, however late it settles and whatever the mode is now. Journaled raw BEFORE
            // any control phrase acts (a line ending in "end prayer" is kept as recognized). Control
            // phrases and the amen rule act only when that capture is still the open prayer: words
            // from an ended prayer never end or alter a newer one.
            journalPrayerASR(captureId: owner, text: cleaned)
            if prayerPartial?.captureId == owner { prayerPartial = nil } // the final covers that utterance
            if owner == mode.prayerCaptureId {
                if handleVoiceCommand(cleaned) { return }
                act(on: floor.observe(text: cleaned, final: true, praying: true, at: floorClock()), text: cleaned)
            } else {
                logLine("final from ended prayer \(owner) journaled; control phrases not applied to the current mode")
            }
            return
        }
        if bug.silencesOutput {
            // Kept beside the server's transcript and sent with the report; never a command.
            bug.deviceLines.append(cleaned)
            return
        }
        if mode.isPraying {
            if addressFinalPending, let captureId = mode.prayerCaptureId, policy.address(startOf: cleaned) != nil {
                // The utterance that opened this prayer by address ended before it could be adopted.
                addressFinalPending = false
                journalPrayerASR(captureId: captureId, text: cleaned)
                act(on: floor.observe(text: cleaned, final: true, praying: true, at: floorClock()), text: cleaned)
                return
            }
            // A conversation request that settled after prayer began: not prayer text, and its
            // control phrases do not touch the prayer.
            logLine("conversation final during prayer ignored (request opened before the prayer); control phrases not applied")
            return
        }
        if handleVoiceCommand(cleaned) {
            floor.newUtterance()
            return
        }
        if case .enterPrayer(let address) = floor.observe(text: cleaned, final: true, praying: false, at: floorClock()) {
            // No partial showed the address: the utterance that just ended is the prayer's beginning.
            beginPrayer(trigger: .address(address))
            addressFinalPending = false
            if let captureId = mode.prayerCaptureId { journalPrayerASR(captureId: captureId, text: cleaned) }
            return
        }
        // Ordinary speech: the talk server answers it from its own audio and transcript; the phone
        // sends nothing for it (a second copy would be a second turn).
    }

    /// Typed input goes to the talk server as `client_text` (labelled typed; not proof of live voice).
    func submitTyped() {
        let text = typedInput.trimmingCharacters(in: .whitespacesAndNewlines)
        typedInput = ""
        guard !text.isEmpty else { return }
        lastTranscript = text
        logLine("final (typed): \(text)")
        if mode.isPraying {
            logLine("typed input not sent during prayer")
            return
        }
        if bug.silencesOutput {
            // Typed while reporting: the talk tier adds it to the report; it is never a turn.
            guard let talk else {
                bug.lines.append(text)
                return
            }
            talk.send(.clientText(text))
            return
        }
        if handleVoiceCommand(text) { return }
        assistantText = ""
        guard let talk else {
            lastError = "talk server not connected; typed text not sent"
            return
        }
        talk.send(.clientText(text))
        if !talk.state.isUp { logLine("talk server not connected yet; typed text will be sent when it is") }
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
        if policy.isQuietCommand(text) { enterQuiet(); return true }
        if policy.isWakeCommand(text) { wake(); return true }
        if !mode.isPraying, policy.isPrayerStartCommand(text) { beginPrayer(trigger: .command); return true }
        if mode.isPraying, policy.isPrayerEndCommand(text) { endPrayer(reason: "end phrase"); return true }
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

    // MARK: bug chip

    /// The Bug chip's tap: opens a report, or files the open one.
    func toggleBugReport() {
        switch bug.phase {
        case .reporting:
            finishBugReport()
        case .filing:
            break
        default:
            startBugReport()
        }
    }

    func startBugReport() {
        guard !mode.isPraying else {
            lastError = "End the prayer before reporting a bug."
            return
        }
        bug.begin()
        talk?.voice.stop()
        devotionalInterrupted(reason: "bug report")
        logLine("bug report started")
        guard let talk else {
            bug.phase = .failed
            bug.detail = "the talk server is not connected"
            return
        }
        talk.send(.clientBug(action: "start", context: bugContext, deviceLines: []))
    }

    func finishBugReport() {
        guard bug.phase == .reporting else { return }
        bug.phase = .filing
        logLine("bug report stopped (\(bug.lines.count) lines heard, \(bug.deviceLines.count) on the phone); filing")
        talk?.send(.clientBug(action: "stop", context: bugContext, deviceLines: bug.deviceLines))
    }

    func cancelBugReport() {
        guard bug.silencesOutput else { return }
        talk?.send(.clientBug(action: "cancel", context: [:], deviceLines: []))
        bug = BugReportState()
        logLine("bug report cancelled")
    }

    /// Clears a filed (or failed) report's card.
    func dismissBugOutcome() {
        guard !bug.silencesOutput else { return }
        bug = BugReportState()
    }

    /// The phone's facts a report carries (flat strings for both transports).
    private var bugContext: [String: String] {
        ["client": "ios", "app_build": PortalModel.appBuild, "install_id": installId,
         "transport": activeTalkTransport?.rawValue ?? "none", "audio_route": audio.sessionDescription]
    }

    /// Stop: silences the bot now (and the rest of its current turn) and tells the server.
    func stopOutput() {
        apply(.stopRequested)
        talk?.send(.clientEvent(name: "stop", detail: [:]))
    }

    private func handle(_ e: ServerEvent, _ raw: [String: Any]) {
        switch e.type {
        case "error":
            lastError = e.message
            logLine("backend error: \(e.message ?? "?")")
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

    // MARK: quiet / wake / prayer

    func enterQuiet() { enterQuiet(reason: "explicit") }
    func wake() { wake(reason: "explicit") }

    func enterQuiet(reason: String) {
        modeReason = reason
        apply(.enterQuiet)
        logLine("quiet entered (\(reason))")
    }

    func wake(reason: String) {
        modeReason = reason
        apply(.wake)
        logLine("woken (\(reason))")
    }

    /// How a prayer began; decides pre-roll and whether the utterance in progress is adopted.
    enum PrayerTrigger: Equatable {
        case button
        case command
        case address(String)
        case server(String)

        var label: String {
            switch self {
            case .button: return "button"
            case .command: return "begin prayer phrase"
            case .address(let phrase): return "address:\(phrase)"
            case .server(let reason): return "server \(reason)"
            }
        }
    }

    func beginPrayer() { beginPrayer(trigger: .button) }

    func beginPrayer(trigger: PrayerTrigger) {
        guard !mode.isPraying else { return }
        let captureId = CaptureManifest.newCaptureId(kind: "prayer")
        var manifest = CaptureManifest(captureId: captureId, kind: "prayer", createdAt: Date())
        manifest.trigger = trigger.label
        captures.insert(manifest, at: 0)
        persistCaptures()
        asrJournals[captureId] = PrayerASRJournal(captureId: captureId)
        modeReason = trigger.label
        apply(.enterPrayer(captureId: captureId))
        floor.reset()
        // From here every recognition request belongs to this capture. An address-triggered prayer
        // keeps the request in progress (its words are the prayer's first words); otherwise an open
        // conversation request is ended and keeps its own identity.
        if case .address = trigger {
            addressFinalPending = !recognizer.adoptCurrent(owner: captureId)
        } else {
            addressFinalPending = false
            recognizer.rebindOwner(captureId)
        }
        // The owner's intent (.running) is not evidence that audio arrives. Check the engine facts,
        // try one restart, and keep observing: the warning tracks delivery for the whole prayer.
        captureWarning = nil
        if audio.state == .running, !isDelivering {
            audio.restartIfNeeded(reason: "begin_prayer")
        } else if audio.state != .running {
            // One start attempt on a real device; never under the XCTest host (permission alert) or fixture mode.
            if !PortalModel.skipPermissionPrompts, !PortalModel.underXCTest { startAudio() }
        }
        let preroll: Double
        switch trigger {
        case .address: preroll = policy.prayerPrerollSeconds
        case .server: preroll = policy.serverPrayerPrerollSeconds
        case .button, .command: preroll = 0
        }
        if audio.state == .running {
            let written = audio.beginCapture(captureId: captureId, directory: store.captureDirectory(captureId), startIndex: 0, prerollSeconds: preroll)
            if written > 0, let i = captures.firstIndex(where: { $0.captureId == captureId }) {
                captures[i].prerollSeconds = (written * 10).rounded() / 10
                persistCaptures()
            }
        }
        logLine("prayer begun (\(trigger.label)); capture \(captureId); pre-roll \(preroll) s requested; recognition continues for the amen rule and end commands")
        refreshDeliveryWarning()
        deliveryWatch?.invalidate()
        deliveryWatch = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refreshDeliveryWarning() }
        }
        startFloorTimer()
        sendSnapshot(reason: "capture")
    }

    /// Ten times a second while praying: the amen window, never silence alone.
    private func startFloorTimer() {
        floorTimer?.invalidate()
        floorTimer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.floorTick() }
        }
    }

    /// Test seam and the timer body.
    func floorTick() {
        let voice: VoiceFacts?
        if let probe = voiceProbe { voice = probe() } else { voice = audio.state == .running ? audio.voiceFacts : nil }
        let decision = floor.tick(praying: mode.isPraying, at: floorClock(), voice: voice)
        if decision == .endPrayer { act(on: .endPrayer, text: "") }
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

    func endPrayer() { endPrayer(reason: "button") }

    func endPrayer(reason: String) {
        guard mode.isPraying else { return }
        if let tail = prayerPartial, tail.captureId == mode.prayerCaptureId {
            journalPrayerASR(captureId: tail.captureId, text: tail.text)
        }
        prayerPartial = nil
        // Ends the prayer's open recognition request without dropping its final (it is still
        // delivered under this capture's identity); later requests are conversation again.
        recognizer.rebindOwner(nil)
        modeReason = reason
        apply(.endPrayer)
        floor.reset()
        floorTimer?.invalidate()
        floorTimer = nil
        deliveryWatch?.invalidate()
        deliveryWatch = nil
        captureWarning = nil
        addressFinalPending = false
        logLine("prayer ended (\(reason))")
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

    func segmentClosed(captureId: String, _ seg: SegmentRecord) {
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
        updateInstallSceneChanged(phase)
        if phase == .active {
            if audio.state == .running, !audio.isRunning { audio.restartIfNeeded(reason: "foreground") }
            sendSnapshot(reason: "foreground")
            startHeartbeat()
            startUpdateChecks()
        } else {
            heartbeat?.invalidate()
            heartbeat = nil
            updateTimer?.invalidate()
            updateTimer = nil
            // Tell the backend the app left the foreground while the socket may still carry it;
            // afterwards it shows connected_background / offline, never live.
            sendSnapshot(reason: scenePhaseLabel)
        }
    }

    // MARK: update banner

    private func startUpdateChecks() {
        Task { await checkForUpdate(reason: "foreground") }
        updateTimer?.invalidate()
        updateTimer = Timer.scheduledTimer(withTimeInterval: AppUpdate.checkInterval, repeats: true) { [weak self] _ in
            Task { await self?.checkForUpdate(reason: "timer") }
        }
    }

    /// Reads latest.json and offers a newer build. Failures (off the tailnet, page down) keep the
    /// current offer and are logged once per distinct error.
    func checkForUpdate(reason: String) async {
        guard let url = URL(string: updateURLString) else { return }
        do {
            let latest = try AppUpdate.decode(try await updateFetcher(url))
            lastUpdateCheckError = nil
            let dismissed = UserDefaults.standard.object(forKey: "dismissedUpdateBuild") as? Int
            let offer = AppUpdate.offer(latest, currentBuild: PortalModel.appBuild, bundleId: Bundle.main.bundleIdentifier, dismissedBuild: dismissed)
            updateCheckStatus = AppUpdate.statusLine(latest: latest.build, currentBuild: PortalModel.appBuild, offered: offer != nil, checkedAt: Date())
            if updatedNotice != nil, latest.build == Int(PortalModel.appBuild) { updatedNotes = latest.notes }
            if offer != updateOffer {
                updateOffer = offer
                logLine(offer.map { "update available: build \($0.build) (\(reason))" } ?? "no newer build (latest \(latest.build), this \(PortalModel.appBuild))")
                sendSnapshot(reason: "update")
            }
        } catch {
            let text = error.localizedDescription
            updateCheckStatus = "check failed \(AppUpdate.clock(Date())): \(text)"
            if text != lastUpdateCheckError {
                lastUpdateCheckError = text
                logLine("update check failed (\(reason)): \(text)")
            }
        }
    }

    /// The banner's Install: opens the build's itms-services link, then follows Apple's prompt
    /// (updateInstallSceneChanged). No prompt within `updatePromptTimeout`, or a refused link,
    /// offers the install page instead.
    func installUpdate() async {
        guard let offer = updateOffer, let url = URL(string: offer.install_url) else { return }
        let build = offer.build
        updateInstall = UpdateInstall(build: build, phase: .opening)
        logLine("update install opened for build \(build)")
        sendSnapshot(reason: "update_install")
        guard await urlOpener(url) else {
            logLine("update install: iOS refused the install link for build \(build)")
            setUpdateInstallPhase(.noPrompt)
            return
        }
        try? await Task.sleep(nanoseconds: UInt64(updatePromptTimeout * 1_000_000_000))
        if updateInstall == UpdateInstall(build: build, phase: .opening) { setUpdateInstallPhase(.noPrompt) }
    }

    /// Follows Apple's install prompt through scene changes: inactive while it shows, active when
    /// it closes. Then, after `updateLeaveDelay`, the app steps to the Home Screen so iOS 26 can
    /// replace it; coming back on the old build reports that (the prompt was cancelled).
    func updateInstallSceneChanged(_ phase: ScenePhase) {
        guard let install = updateInstall else { return }
        switch (install.phase, phase) {
        case (.opening, .inactive):
            setUpdateInstallPhase(.prompt)
        case (.prompt, .active):
            setUpdateInstallPhase(.leaving)
            Task { [weak self] in
                guard let self else { return }
                try? await Task.sleep(nanoseconds: UInt64(self.updateLeaveDelay * 1_000_000_000))
                guard self.updateInstall == UpdateInstall(build: install.build, phase: .leaving) else { return }
                if self.appSuspender() {
                    self.setUpdateInstallPhase(.suspended)
                } else {
                    self.setUpdateInstallPhase(.closeManually)
                }
            }
        case (.leaving, .background):
            setUpdateInstallPhase(.suspended)
        case (.suspended, .active):
            setUpdateInstallPhase(.returned)
        default:
            break
        }
    }

    /// The banner's Open page: the build's install page in Safari (when no prompt appeared).
    func openUpdatePage() async {
        guard let offer = updateOffer, let page = offer.page_url.flatMap(URL.init(string:)) else { return }
        updateInstall = nil
        logLine("update install: opening the install page for build \(offer.build)")
        _ = await urlOpener(page)
    }

    /// The banner's dismiss: this build is not offered again; a later one is. Ignored during the
    /// hand-off, when iOS may already have queued the install.
    func dismissUpdate() {
        guard let offer = updateOffer, updateInstall?.inFlight != true else { return }
        UserDefaults.standard.set(offer.build, forKey: "dismissedUpdateBuild")
        updateOffer = nil
        updateInstall = nil
        logLine("update to build \(offer.build) dismissed")
    }

    func dismissUpdatedNotice() {
        updatedNotice = nil
        updatedNotes = nil
    }

    private func setUpdateInstallPhase(_ phase: UpdateInstall.Phase) {
        guard var install = updateInstall else { return }
        install.phase = phase
        updateInstall = install
        logLine("update install: build \(install.build) \(phase.rawValue)")
        sendSnapshot(reason: "update_install")
    }

    /// Sends the app to the Home Screen like the Home gesture; false when UIKit lacks it.
    static func suspendToHomeScreen() -> Bool {
        // No public API backgrounds an app, and iOS 26 will not install over a foreground one.
        // `suspend` is UIKit's private selector; this personal Ad Hoc app never goes to App Review.
        let selector = NSSelectorFromString("suspend")
        guard UIApplication.shared.responds(to: selector) else { return false }
        UIApplication.shared.perform(selector)
        return true
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
            last_error: lastError,
            talk_transport: talkTransportRaw,
            talk_transport_active: activeTalkTransport?.rawValue,
            talk_url: talkURLString,
            talk_state: talkState.label,
            talk_detail: talk?.detail,
            bot_speaking: botSpeaking,
            voice_output: talk?.voice.statusLine,
            webrtc_spike: webrtcSpike,
            policy_source: policySource,
            audio_tuning: AudioTuning.current.description,
            update_offer: updateOffer.map { "build \($0.build)" },
            update_check: updateCheckStatus,
            update_install: updateInstall.map { "build \($0.build) \($0.phase.rawValue)" },
            updated_notice: updatedNotice,
            bug_report: "\(bug.phase) lines \(bug.lines.count) device \(bug.deviceLines.count)\(bug.ticketId.map { " \($0)" } ?? "")\(bug.outcome.map { " — \($0)" } ?? "")"
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
        case .bug_report:
            let action = command.args?["action"]?.stringValue ?? ""
            switch action {
            case "start": startBugReport()
            case "stop": finishBugReport()
            case "cancel": cancelBugReport()
            case "text":
                typedInput = command.args?["text"]?.stringValue ?? ""
                submitTyped()
            default:
                report(CommandResult(commandId: command.command_id, status: .failed, detail: ["reason": "action must be start, stop, cancel or text"]))
                return
            }
            report(CommandResult(commandId: command.command_id, status: .applied, detail: ["action": action, "phase": "\(bug.phase)", "lines": "\(bug.lines.count)"]))
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
             "trigger": c.trigger as Any, "preroll_s": c.prerollSeconds as Any, "talk_prayer_text_lines": talkPrayerLines[c.captureId] as Any,
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
        guard connection.isConnected else { return }
        uploadRequested.insert(captureId)
        guard uploadingCaptures.insert(captureId).inserted else { return }
        defer {
            uploadingCaptures.remove(captureId)
            uploadRequested.remove(captureId)
        }
        repeat {
            uploadRequested.remove(captureId)
            await uploadPendingPass(captureId: captureId)
        } while connection.isConnected && uploadRequested.contains(captureId)
    }

    private func uploadPendingPass(captureId: String) async {
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

    // MARK: talk tier (the app's voice)

    var selectedTalkTransport: TalkTransportKind { TalkTransportKind(rawValue: talkTransportRaw) ?? .webrtc }

    /// The transport actually run: fixture mode never opens WebRTC (it would open the microphone
    /// itself), and a WebRTC coexistence failure recorded on this build falls back to WebSocket.
    var effectiveTalkTransport: TalkTransportKind {
        if PortalModel.skipPermissionPrompts { return .websocket }
        if selectedTalkTransport == .webrtc, webrtcFallbackReason != nil { return .websocket }
        return selectedTalkTransport
    }

    /// (Re)creates the talk client for the effective transport and connects it.
    func connectTalk() {
        talk?.disconnect()
        stopSpikeMonitor()
        let kind = effectiveTalkTransport
        let made: TalkClient
        if let factory = talkFactory {
            made = factory(kind)
        } else {
            switch kind {
            case .webrtc: made = WebRTCTalkClient()
            case .websocket: made = WebSocketTalkClient(audio: audio)
            }
        }
        made.onEvent = { [weak self] event in self?.handleTalk(event) }
        made.onState = { [weak self] state in self?.talkStateChanged(state) }
        made.onLog = { [weak self] line in self?.logLine(line) }
        made.voice.onPlayingChange = { [weak self] playing in Task { @MainActor in self?.botAudible = playing } }
        made.voice.gateOpen = speechGateOpen
        talk = made
        activeTalkTransport = kind
        talkState = made.state
        botSpeaking = false
        guard let url = URL(string: talkURLString), url.host != nil else {
            logLine("talk URL invalid: \(talkURLString)")
            return
        }
        logLine("talk connecting: \(kind.label) \(url.absoluteString)\(kind != selectedTalkTransport ? " (selected \(selectedTalkTransport.label); \(webrtcFallbackReason ?? "fixture mode"))" : "")")
        made.connect(baseURL: url)
    }

    func setTalkURL(_ s: String) {
        let trimmed = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let url = URL(string: trimmed), url.host != nil else { return }
        talkURLString = trimmed
        connectTalk()
    }

    /// Diagnostics picker. Selecting WebRTC again clears a recorded coexistence fallback.
    func setTalkTransport(_ kind: TalkTransportKind) {
        talkTransportRaw = kind.rawValue
        if kind == .webrtc, webrtcFallbackReason != nil {
            webrtcFallbackReason = nil
            UserDefaults.standard.removeObject(forKey: "webrtcFallback-\(PortalModel.appBuild)")
            logLine("WebRTC selected again; coexistence fallback cleared")
        }
        connectTalk()
    }

    private func talkStateChanged(_ state: TalkConnectionState) {
        talkState = state
        if talkLoggedState != state.label {
            talkLoggedState = state.label
            logLine("talk \(activeTalkTransport?.label ?? "?") \(state.label)")
        }
        if state.isUp {
            // The phone's persisted mode is the authority: the server is told on every (re)connect.
            sendClientMode(ClientFloorMode.of(mode), reason: "sync")
            // So is the pane's passage: a new talk session opens it again (it keeps its position when
            // the server still holds it).
            if let reference = passage?.reference ?? UserDefaults.standard.string(forKey: "panePassage") {
                talk?.send(.clientPassage(reference: reference, reason: "sync"))
            }
            if activeTalkTransport == .webrtc { startSpikeMonitor() }
            if let ciBug = ProcessInfo.processInfo.environment["RLMADK_CI_BUG"], !ciDemoDone {
                // CI screenshots of the Bug chip: a report open with the fixture's lines ("reporting"),
                // or opened and filed ("filed").
                ciDemoDone = true
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                    startBugReport()
                    guard ciBug == "filed" else { return }
                    try? await Task.sleep(nanoseconds: 3_500_000_000)
                    finishBugReport()
                }
            }
            if ProcessInfo.processInfo.environment["RLMADK_CI_DEMO"] == "1", !ciDemoDone {
                // CI demo: a typed turn against the fixture talk server so the screenshot shows a reply.
                ciDemoDone = true
                typedInput = "What does the shepherd psalm say about rest?"
                submitTyped()
                // And the scripture pane: open Psalm 23 and read it (the fixture server times the verses).
                Task { @MainActor in
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                    openPassage("Psalm 23")
                    try? await Task.sleep(nanoseconds: 1_500_000_000)
                    toggleReading()
                }
            }
        } else {
            botSpeaking = false
            bug.connectionLost()
            if activeTalkTransport == .webrtc { spikeNotDeliveringSince = nil }
        }
        sendSnapshot(reason: "talk")
    }

    /// Test seam: delivers a talk event as if the active client had.
    func talkEventReceived(_ event: TalkEvent) { handleTalk(event) }

    private func handleTalk(_ event: TalkEvent) {
        switch event {
        case .botReady:
            logLine("talk bot ready")
        case .userTranscript(let text, let final):
            serverHeard = text
            if final {
                assistantText = ""
                // A new user turn ends any reading in progress.
                devotionalInterrupted(reason: "user spoke")
            }
        case .userStartedSpeaking, .userStoppedSpeaking:
            break
        case .botTranscript(let text):
            let piece = text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !piece.isEmpty else { return }
            assistantText = assistantText.isEmpty ? piece : assistantText + " " + piece
        case .botSpokenText(let text):
            devotionalSpoken(text)
        case .botStartedSpeaking:
            botTurnCounter += 1
            botSpeaking = true
            talk?.voice.botTurnStarted()
            apply(.botStartedSpeaking(turnId: "bot-\(botTurnCounter)"))
        case .botStoppedSpeaking:
            botSpeaking = false
            talk?.voice.botTurnEnded()
            apply(.botStoppedSpeaking)
        case .interruption:
            // The WebSocket client already flushed its player on the socket queue.
            talk?.voice.flush()
            devotionalInterrupted(reason: "interruption")
        case .floorMode(let serverMode, _, let reason):
            applyServerFloor(serverMode, reason: reason ?? "server")
        case .assistantName(let name):
            if name != assistantName {
                assistantName = name
                UserDefaults.standard.set(name, forKey: "assistantName")
                logLine("talk assistant: \(name)")
            }
        case .prayerText(let text, let final):
            journalTalkPrayerText(text, final: final)
        case .passage(let p):
            passage = p
            passageRequested = nil
            reading.reset(for: p)
            UserDefaults.standard.set(p.reference, forKey: "panePassage")
            logLine("pane: \(p.reference) (\(p.verses.count) verses, \(p.source ?? "?"), heard \(p.spokenCount))")
        case .passageError(let reference, let reason):
            passageRequested = nil
            lastError = "Could not open \(reference): \(reason)"
            logLine("pane: \(reference) unavailable: \(reason)")
        case .bugReport(let event):
            bug.apply(event)
            logLine("bug report \(event.state)\(event.ticketId.map { " \($0)" } ?? "")\(event.title.map { ": \($0)" } ?? "")\(event.error.map { " (\($0))" } ?? "")")
            if event.state == "filed" || event.state == "spooled" || event.state == "triaged" { sendSnapshot(reason: "bug") }
        case .bugText(let text, let final):
            bug.heard(text, final: final)
            if final { serverHeard = text }
        case .reading(let event):
            if event.reference == nil || event.reference == passage?.reference {
                let verses = passage?.verses ?? []
                reading.apply(event) { i in verses.indices.contains(i) ? verses[i].text : nil }
                passage?.spokenCount = reading.spokenCount
            }
            if event.state == "blocked" { lastError = "Reading is held while \(event.reason ?? "quiet")" }
        case .error(let message):
            lastError = "talk: \(message)"
            logLine("talk error: \(message.prefix(200))")
        }
    }

    /// The server's floor follows the phone, but the server can also detect an address or an amen
    /// the phone missed (its own utterance segmentation); the phone then follows it so both agree.
    private func applyServerFloor(_ serverMode: String, reason: String) {
        logLine("talk floor: server \(serverMode) (\(reason))")
        if reason.hasPrefix("client:") { return } // the echo of our own client_mode
        switch serverMode {
        case "prayer":
            if !mode.isPraying { beginPrayer(trigger: .server(reason)) }
        case "available":
            if mode.isPraying {
                endPrayer(reason: "server \(reason)")
            } else if mode.quiet, reason == "wake" {
                wake(reason: "server wake")
            }
        case "quiet":
            if !mode.quiet { enterQuiet(reason: "server \(reason)") }
        default:
            break
        }
    }

    /// Server-side prayer transcript (Cohere, per utterance), kept with the open prayer capture
    /// beside the on-device journal. Never sent anywhere from here.
    private func journalTalkPrayerText(_ text: String, final: Bool) {
        guard final, let captureId = mode.prayerCaptureId ?? captures.first(where: { $0.kind == "prayer" })?.captureId else { return }
        do {
            try store.appendTalkPrayerText(captureId: captureId, text: text)
            talkPrayerLines[captureId, default: 0] += 1
        } catch {
            logLine("prayer_text journal write FAILED for \(captureId): \(error.localizedDescription)")
        }
    }

    // MARK: WebRTC coexistence spike

    /// Observes the audio owner every second while WebRTC is up: whether tap buffers still arrive,
    /// the engine's running flag, the age of the last buffer and the growth of the open segment
    /// file. A capture that stops for more than 3 s gets one restart; if it stays stopped, the
    /// failure is recorded with its evidence and the app falls back to the WebSocket transport.
    private func startSpikeMonitor() {
        guard spikeTimer == nil else { return }
        spikeConnectedAt = Date()
        spikeBaselineBytes = openSegmentBytes()
        spikeBaselineFile = audio.openSegmentURL?.lastPathComponent
        spikeBaselineSegments = captures.reduce(0) { $0 + $1.segments.count }
        spikeNotDeliveringSince = nil
        spikeRestartTried = false
        spikeSuccessRecorded = false
        recordSpike("connected", detail: "baseline")
        spikeTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.spikeTick() }
        }
    }

    private func stopSpikeMonitor() {
        spikeTimer?.invalidate()
        spikeTimer = nil
    }

    private func openSegmentBytes() -> Int? {
        guard let url = audio.openSegmentURL else { return nil }
        return (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? nil
    }

    private func spikeFacts() -> String {
        let age = audio.lastBufferAge.map { String(format: "%.1f", $0) } ?? "never"
        let bytes = openSegmentBytes()
        let file = audio.openSegmentURL?.lastPathComponent
        let closedSince = captures.reduce(0) { $0 + $1.segments.count } - spikeBaselineSegments
        let growth: String
        if let bytes, let base = spikeBaselineBytes, file == spikeBaselineFile {
            growth = "\(bytes - base) bytes in \(file ?? "-")"
        } else {
            growth = "segment rotated (\(spikeBaselineFile ?? "-") -> \(file ?? "-"))"
        }
        return "audio_owner_state=\(audio.state.label) engine_running=\(audio.isRunning) last_buffer_age_s=\(age) open_segment=\(file ?? "-") open_segment_bytes=\(bytes.map(String.init) ?? "-") growth_since_connect=\(growth) segments_closed_since_connect=\(closedSince) watchdog_restarts=\(watchdogRestarts) \(talk?.voice.statusLine ?? "") session[\(audio.sessionDescription)]"
    }

    private func recordSpike(_ status: String, detail: String) {
        let line = "webrtc coexistence \(status) (\(detail)) at \(ISO8601DateFormatter().string(from: Date())) build \(PortalModel.appBuild): \(spikeFacts())"
        webrtcSpike = line
        logLine(line)
        store.appendSpike(line)
        sendSnapshot(reason: "talk")
    }

    private func spikeTick() {
        guard activeTalkTransport == .webrtc, talkState.isUp, let connectedAt = spikeConnectedAt else { return }
        let now = Date()
        if PortalModel.underXCTest || audio.state != .running { return }
        if isDelivering {
            spikeNotDeliveringSince = nil
            if !spikeSuccessRecorded, now.timeIntervalSince(connectedAt) >= 30 {
                spikeSuccessRecorded = true
                recordSpike("holding", detail: "capture delivered for 30 s with WebRTC up")
            }
            return
        }
        let since = spikeNotDeliveringSince ?? now
        spikeNotDeliveringSince = since
        guard now.timeIntervalSince(since) > 3 else { return }
        if !spikeRestartTried {
            spikeRestartTried = true
            spikeNotDeliveringSince = now
            recordSpike("capture stalled", detail: "no tap buffer for 3 s; restarting the engine once")
            audio.restartIfNeeded(reason: "webrtc_coexistence")
            return
        }
        recordSpike("FAILED", detail: "capture still stalled after a restart; falling back to WebSocket")
        let reason = "capture stopped with WebRTC up (build \(PortalModel.appBuild), \(ISO8601DateFormatter().string(from: now)))"
        webrtcFallbackReason = reason
        UserDefaults.standard.set(reason, forKey: "webrtcFallback-\(PortalModel.appBuild)")
        connectTalk()
        audio.restartIfNeeded(reason: "webrtc_fallback")
    }

    // MARK: scripture pane

    /// Opens `reference` in the pane: through the talk tier (it pauses any reading and becomes the
    /// passage the assistant answers about), or from the backend's reader when the talk tier is down.
    func openPassage(_ reference: String) {
        let ref = reference.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !ref.isEmpty else { return }
        passageRequested = ref
        if let talk, talkState.isUp {
            talk.send(.clientPassage(reference: ref, reason: "select"))
            logLine("pane: requested \(ref)")
        } else {
            Task { await loadPassageFromBackend(ref) }
        }
    }

    func openChapter(_ book: BibleBook, _ chapter: Int) { openPassage(book.reference(chapter: chapter)) }

    /// The previous (-1) or next (+1) chapter of the open passage.
    func stepChapter(_ step: Int) {
        guard let p = passage, let code = p.book, let chapter = p.chapter, let (book, next) = BibleBook.neighbour(of: code, chapter: chapter, step: step) else { return }
        openChapter(book, next)
    }

    /// The pane's speaker button: read the open passage from where it stopped, or pause it.
    func toggleReading() {
        guard passage != nil else { return }
        if reading.isReading {
            talk?.send(.clientRead(action: "pause"))
        } else if talkState.isUp {
            if mode.quiet { lastError = "The portal is quiet; wake it to hear the reading." }
            talk?.send(.clientRead(action: "play"))
        } else {
            lastError = "talk server not connected; reading not requested"
        }
    }

    private struct ScriptureToolResult: Decodable {
        var available: Bool
        var reference: String?
        var title: String?
        var verses: [ScriptureVerse]?
        var spoken: String?
        struct Ref: Decodable { var book: String?; var chapter: Int? }
        var references: [Ref]?
    }

    private func loadPassageFromBackend(_ reference: String) async {
        do {
            let r = try await client.get("tools/scripture", query: ["reference": reference], as: ScriptureToolResult.self)
            guard r.available, let verses = r.verses, !verses.isEmpty else {
                passageRequested = nil
                lastError = "Could not open \(reference): \(r.spoken ?? "unavailable")"
                return
            }
            let first = r.references?.first
            let p = ScripturePassage(reference: r.reference ?? reference, title: r.title, edition: "World English Bible", book: first?.book, chapter: first?.chapter, verses: verses, spokenCount: 0, source: "backend")
            handleTalk(.passage(p))
        } catch {
            passageRequested = nil
            lastError = "Could not open \(reference): \(error.localizedDescription)"
        }
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

    /// Asks the talk tier to read the passage from the saved position. Position then follows what
    /// the bot actually speaks (`bot-tts-text`), aligned to the segments by `DevotionalTracker`.
    func playDevotional() {
        guard let d = devotional, let segments = d.segments, !segments.isEmpty else { return }
        guard SpeechGate.mayEnqueue(mode) else { logLine("devotional playback suppressed by gate (\(mode.label))"); return }
        guard let talk else { lastError = "talk server not connected; devotional not requested"; return }
        let revision = d.revision_hash ?? ""
        if progress == nil || !(progress!.matches(passage: d.passage, revision: revision)) || progress!.total != segments.count {
            progress = DevotionalProgress(passage: d.passage, revision: revision, total: segments.count)
        }
        var start = progress!.resumeIndex
        if progress!.finished { start = 0; progress!.finished = false }
        devotionalTracker = DevotionalTracker(segments: segments.map(\.text), startIndex: start)
        devotionalPlaying = true
        let from = segments[start].osis.map { "from \($0)" } ?? "from its segment \(start + 1)"
        talk.send(.clientText("Please read \(d.title ?? d.passage) aloud \(from) to the end of the passage, exactly as the retained text reads, with no commentary."))
        logLine("devotional requested from segment \(start + 1)/\(segments.count) (\(d.passage) @ \(revision.prefix(12)))")
    }

    func pauseDevotional() {
        stopOutput()
        devotionalInterrupted(reason: "paused")
        logLine("devotional paused at segment \((progress?.resumeIndex ?? 0) + 1)")
    }

    private func devotionalSpoken(_ text: String) {
        guard devotionalPlaying, var tracker = devotionalTracker else { return }
        let updates = tracker.spoken(text)
        devotionalTracker = tracker
        for update in updates {
            switch update {
            case .started(let i): progress?.started(i)
            case .finished(let i): progress?.finished(i)
            }
        }
        if !updates.isEmpty { persistProgress() }
        if tracker.isComplete {
            devotionalPlaying = false
            devotionalTracker = nil
            logLine("devotional reading complete")
        }
    }

    /// Interruption keeps the segment that was being read, so continue repeats it in full.
    private func devotionalInterrupted(reason: String) {
        guard devotionalPlaying else { return }
        devotionalPlaying = false
        devotionalTracker = nil
        progress?.interrupted()
        persistProgress()
        logLine("devotional interrupted (\(reason)) at segment \((progress?.resumeIndex ?? 0) + 1)")
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

    init(root: URL? = nil) {
        self.root = root ?? FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        try? FileManager.default.createDirectory(at: self.root.appendingPathComponent("captures"), withIntermediateDirectories: true)
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

    /// Server-side prayer transcript (`prayer_text` from the talk tier), one JSON line each, in
    /// `Documents/prayer-talk/<captureId>.jsonl`. Kept beside the on-device journal, not merged.
    func appendTalkPrayerText(captureId: String, text: String) throws {
        let dir = root.appendingPathComponent("prayer-talk")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        var data = try JSONSerialization.data(withJSONObject: ["client_time": Date().timeIntervalSince1970, "text": text, "source": "talk_server"])
        data.append(0x0A)
        let url = dir.appendingPathComponent("\(captureId).jsonl")
        if FileManager.default.fileExists(atPath: url.path) {
            let handle = try FileHandle(forWritingTo: url)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: data)
        } else {
            try data.write(to: url, options: .atomic)
        }
    }

    /// WebRTC coexistence observations, one line each, in `Documents/webrtc-spike.log`.
    func appendSpike(_ line: String) {
        let url = root.appendingPathComponent("webrtc-spike.log")
        let data = Data((line + "\n").utf8)
        if let handle = try? FileHandle(forWritingTo: url) {
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
            try? handle.close()
        } else {
            try? data.write(to: url, options: .atomic)
        }
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
