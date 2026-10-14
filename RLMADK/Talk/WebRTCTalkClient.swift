import AVFoundation
import Foundation
import PipecatClientIOS
import PipecatClientIOSSmallWebrtc
import WebRTC

/// Bot audio on the WebRTC transport plays through libwebrtc's own audio unit, which the app does
/// not own, and that unit also carries the WebRTC microphone. Build 12 closed the gate at the unit
/// (`isAudioEnabled` false in prayer, quiet or after a stop), which silenced the bot but also
/// stopped the microphone: a session that entered quiet was deaf to the wake phrase and the talk
/// tier heard nothing in prayer (voicesim WebRTC subset 2026-10-14, 0 of 5). The talk tier's floor
/// already gates every spoken frame in prayer and quiet and interrupts on barge-in, so from build 13
/// the unit stays enabled for the whole connection: `gateOpen` and `stop()` are recorded for the
/// status line but never touch the unit. The floor messages (`mode`, `node`) and the app's own
/// `client_mode` keep both sides agreeing on when speech is allowed.
final class WebRTCVoiceOutput: VoiceOutput {
    var onPlayingChange: ((Bool) -> Void)?
    private var _gateOpen = true
    private var suppressed = false
    private var botSpeaking = false
    /// Set by the client while a session is up.
    var connected = false { didSet { apply() } }
    private(set) var unitToggles = 0

    var gateOpen: Bool {
        get { _gateOpen }
        set {
            if newValue, !_gateOpen { suppressed = false } // a turn cut by the gate is over
            _gateOpen = newValue
            apply()
        }
    }

    var isPlaying: Bool { botSpeaking && !suppressed && _gateOpen && RTCAudioSession.sharedInstance().isAudioEnabled }

    var statusLine: String {
        let rtc = RTCAudioSession.sharedInstance()
        return "webrtc unit: \(rtc.isAudioEnabled ? "enabled" : "disabled") (manual \(rtc.useManualAudio ? "yes" : "no"), active \(rtc.isActive ? "yes" : "no"), toggles \(unitToggles)) · gate \(_gateOpen ? "open" : "closed")\(suppressed ? ", turn suppressed" : "")"
    }

    /// The talk tier interrupts its own output on barge-in (`interruption` / `bot-interrupted`);
    /// the unit is never stopped here because it carries the microphone.
    func stop() {
        if botSpeaking {
            suppressed = true
            onPlayingChange?(isPlaying)
        }
    }

    func flush() {}

    func botTurnStarted() {}

    func botTurnEnded() {
        suppressed = false
        apply()
    }

    func botSpeaking(_ speaking: Bool) {
        botSpeaking = speaking
        onPlayingChange?(isPlaying)
    }

    private func apply() {
        // The unit carries the microphone: it runs whenever connected (see the type comment).
        let enable = connected
        let rtc = RTCAudioSession.sharedInstance()
        guard rtc.isAudioEnabled != enable else { return }
        rtc.isAudioEnabled = enable
        unitToggles += 1
        onPlayingChange?(isPlaying)
    }
}

/// SmallWebRTC through the official Pipecat Swift client.
///
/// Start endpoint: `{base}/start` with `{"transport":"webrtc"}`; the SDK then posts the offer to
/// `/sessions/{id}/api/offer`. RTVI events map to `TalkEvent`; our floor messages arrive as RTVI
/// server messages. Typed text goes up as RTVI `send-text`; `client_mode` and `client_event` go up
/// as RTVI client messages (`t` = the message type, `d` = its fields).
///
/// Coexistence with the audio owner (the spike in ios-client.md): before the first connection the
/// app configures libwebrtc's audio session to the audio owner's own category, mode, options,
/// sample rate and IO buffer, holds one extra activation so libwebrtc's end of session never
/// deactivates the shared session, uses manual audio so its unit runs only when the gate allows,
/// tells the SDK's audio manager that the current route is the preferred device (so it does not
/// reconfigure the session at start) and stops that manager once connected.
@MainActor
final class WebRTCTalkClient: NSObject, TalkClient {
    let kind: TalkTransportKind = .webrtc
    private(set) var state: TalkConnectionState = .disconnected(nil)
    var onEvent: ((TalkEvent) -> Void)?
    var onState: ((TalkConnectionState) -> Void)?
    var onLog: ((String) -> Void)?
    private let webrtcVoice = WebRTCVoiceOutput()
    var voice: VoiceOutput { webrtcVoice }
    private var client: PipecatClient?
    private var baseURL: URL?
    private var wantConnected = false
    private var backoff: Double = 1
    private var generation = 0
    private var pending: [TalkClientMessage] = []
    private var lastTransportState = "-"
    private var sessionId: String?
    private static var audioSessionPrepared = false

    override init() {
        super.init()
        RTCAudioSession.sharedInstance().add(self)
    }

    var detail: String {
        "\(baseURL?.absoluteString ?? "-") session \(sessionId?.prefix(8) ?? "-") · transport \(lastTransportState) · \(webrtcVoice.statusLine)"
    }

    func connect(baseURL: URL) {
        self.baseURL = baseURL
        wantConnected = true
        guard client == nil else { return }
        open()
    }

    func disconnect() {
        wantConnected = false
        generation += 1
        webrtcVoice.connected = false
        let old = client
        client = nil
        Task { try? await old?.disconnect() }
        setState(.disconnected(nil))
    }

    func send(_ message: TalkClientMessage) {
        guard state == .ready, let client else {
            if case .clientMode = message { pending.removeAll { if case .clientMode = $0 { return true } else { return false } } }
            pending.append(message)
            return
        }
        do {
            if case .clientText(let text) = message {
                // NOTE [Context]: RTVI send-text is handled by Pipecat's own RTVI processor (user
                // message appended, LLM run), so typed input and devotional requests work over
                // WebRTC with the server as deployed; the floor messages need the server to unwrap
                // client-message (ios-client.md, server work item 2).
                try client.sendText(content: text)
            } else {
                let data = Value.object(message.fields.mapValues { Value.string($0) })
                try client.sendClientMessage(msgType: message.type, data: data)
            }
        } catch {
            onLog?("talk webrtc send \(message.type) failed: \(error.localizedDescription)")
        }
    }

    // MARK: connection

    /// One-time libwebrtc audio session setup, matched to the audio owner's configuration.
    private static func prepareAudioSession(log: (String) -> Void) {
        guard !audioSessionPrepared else { return }
        audioSessionPrepared = true
        let session = AVAudioSession.sharedInstance()
        let config = RTCAudioSessionConfiguration.webRTC()
        config.category = AVAudioSession.Category.playAndRecord.rawValue
        config.mode = AudioTuning.current.mode.sessionMode.rawValue
        config.categoryOptions = [.allowBluetooth, .defaultToSpeaker]
        if session.sampleRate > 0 { config.sampleRate = session.sampleRate }
        if session.ioBufferDuration > 0 { config.ioBufferDuration = session.ioBufferDuration }
        RTCAudioSessionConfiguration.setWebRTC(config)
        let rtc = RTCAudioSession.sharedInstance()
        rtc.useManualAudio = true
        rtc.isAudioEnabled = false
        rtc.lockForConfiguration()
        do {
            // NOTE [Context]: libwebrtc deactivates AVAudioSession when its own activation count
            // returns to zero, which would stop the audio owner's engine mid-capture. One activation
            // held here for the life of the process keeps that count above zero.
            try rtc.setActive(true)
        } catch {
            log("rtc audio session activation failed: \(error.localizedDescription)")
        }
        rtc.unlockForConfiguration()
        log("rtc audio session prepared: manual audio, \(config.category) \(config.mode) rate \(Int(config.sampleRate)) io \(String(format: "%.3f", config.ioBufferDuration))")
    }

    /// The SDK's device id for the route in use, so its audio manager sees no change to apply.
    private static func currentDeviceId() -> String {
        let port = AVAudioSession.sharedInstance().currentRoute.outputs.first?.portType
        switch port {
        case .some(.bluetoothA2DP), .some(.bluetoothHFP), .some(.bluetoothLE): return "bluetooth"
        case .some(.headphones): return "wired"
        case .some(.builtInReceiver): return "earpiece"
        default: return "speakerphone"
        }
    }

    private func open() {
        guard let baseURL else { return }
        generation += 1
        let mine = generation
        WebRTCTalkClient.prepareAudioSession { [weak self] line in self?.onLog?(line) }
        let transport = SmallWebRTCTransport()
        let client = PipecatClient(options: PipecatClientOptions(transport: transport, enableMic: true, enableCam: false))
        client.delegate = self
        self.client = client
        setState(.connecting)
        let device = WebRTCTalkClient.currentDeviceId()
        let start = APIRequest(endpoint: baseURL.appendingPathComponent("start"), requestData: .object(["transport": .string("webrtc")]), timeout: 20)
        Task { [weak self] in
            do {
                try await client.updateMic(micId: MediaDeviceId(id: device))
                let result: SmallWebRTCStartBotResult = try await client.startBotAndConnect(startBotParams: start)
                guard let self, mine == self.generation else { return }
                _ = result
                // The SDK's audio manager re-applies its own category on every route change;
                // once connected it has nothing more to do for us.
                client.release()
                self.onLog?("talk webrtc connected (preferred device \(device)); sdk audio manager released")
            } catch {
                guard let self, mine == self.generation else { return }
                self.failed("connect: \(error.localizedDescription)")
            }
        }
    }

    private func failed(_ reason: String) {
        webrtcVoice.connected = false
        let old = client
        client = nil
        Task { try? await old?.disconnect() }
        setState(.disconnected(reason))
        onLog?("talk webrtc down: \(reason)")
        guard wantConnected else { return }
        let delay = backoff
        backoff = min(backoff * 2, 15)
        let mine = generation
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            Task { @MainActor in
                guard let self, self.wantConnected, self.client == nil, mine == self.generation else { return }
                self.open()
            }
        }
    }

    private func setState(_ next: TalkConnectionState) {
        guard next != state else { return }
        state = next
        onState?(next)
        if next == .ready {
            backoff = 1
            let queued = pending
            pending.removeAll()
            for message in queued { send(message) }
        }
    }

    private func deliver(_ event: TalkEvent) { onEvent?(event) }
}

extension WebRTCTalkClient: PipecatClientDelegate {
    nonisolated func onTransportStateChanged(state: TransportState) {
        Task { @MainActor in
            self.lastTransportState = state.description
            switch state {
            case .connected: self.webrtcVoice.connected = true; self.setState(.connected)
            case .ready: self.webrtcVoice.connected = true; self.setState(.ready)
            case .disconnected, .error:
                if self.client != nil, self.state != .connecting || state == .error {
                    self.failed("transport \(state.description)")
                }
            default: break
            }
        }
    }

    nonisolated func onBotReady(botReadyData: BotReadyData) {
        Task { @MainActor in
            self.onLog?("talk webrtc bot ready (rtvi \(botReadyData.version))")
            self.setState(.ready)
            self.deliver(.botReady)
        }
    }

    nonisolated func onBotStarted(botResponse: Any) {
        Task { @MainActor in self.onLog?("talk webrtc session started") }
    }

    nonisolated func onUserTranscript(data: Transcript) {
        Task { @MainActor in self.deliver(.userTranscript(text: data.text, final: data.final ?? false)) }
    }

    nonisolated func onBotOutput(data: BotOutputData) {
        // Same rule as the WebSocket parser: display a sentence once, when queued to be spoken.
        guard data.willBeSpoken != false else { return }
        let isNew = data.spokenStatus == .new || data.spokenStatus == nil
        guard isNew else { return }
        Task { @MainActor in self.deliver(.botTranscript(text: data.text)) }
    }

    nonisolated func onBotTtsText(data: BotTTSText) {
        Task { @MainActor in self.deliver(.botSpokenText(text: data.text)) }
    }

    nonisolated func onBotStartedSpeaking() {
        Task { @MainActor in
            self.webrtcVoice.botSpeaking(true)
            self.deliver(.botStartedSpeaking)
        }
    }

    nonisolated func onBotStoppedSpeaking() {
        Task { @MainActor in
            self.webrtcVoice.botSpeaking(false)
            self.deliver(.botStoppedSpeaking)
        }
    }

    nonisolated func onUserStartedSpeaking() {
        Task { @MainActor in self.deliver(.userStartedSpeaking) }
    }

    nonisolated func onUserStoppedSpeaking() {
        Task { @MainActor in self.deliver(.userStoppedSpeaking) }
    }

    nonisolated func onServerMessage(data: Any) {
        // Our floor messages (`mode`, `prayer_text`) as RTVI server messages.
        guard let value = data as? Value, let json = try? JSONEncoder().encode(value),
              let object = (try? JSONSerialization.jsonObject(with: json)) as? [String: Any] else { return }
        let events = TalkMessageParser.events(from: object)
        Task { @MainActor in for event in events { self.deliver(event) } }
    }

    nonisolated func onError(message: RTVIMessageInbound) {
        Task { @MainActor in self.deliver(.error(message.data ?? message.type ?? "error")) }
    }

    nonisolated func onDisconnected() {
        Task { @MainActor in
            if self.client != nil, self.wantConnected { self.failed("disconnected") }
        }
    }
}

extension WebRTCTalkClient: RTCAudioSessionDelegate {
    /// libwebrtc's audio unit started (bot playback and WebRTC microphone running).
    nonisolated func audioSessionDidStartPlayOrRecord(_ session: RTCAudioSession) {
        Task { @MainActor in self.onLog?("rtc audio unit started (play/record)") }
    }

    nonisolated func audioSessionDidStopPlayOrRecord(_ session: RTCAudioSession) {
        Task { @MainActor in self.onLog?("rtc audio unit stopped") }
    }
}
