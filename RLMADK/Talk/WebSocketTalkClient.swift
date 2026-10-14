import Foundation

/// The talk tier's tagged-PCM WebSocket transport (our own protocol, no SDK).
///
/// `POST {base}/start {"transport":"websocket"}` returns a `wsUrl`; only its path (and token, when
/// the runner issues one) is used, against the host and port the app called, because the runner
/// advertises its own idea of host and scheme. Up: the audio owner's capture as 16 kHz int16 mono
/// in 20 ms tagged frames, silence while no capture arrives. Down: 24 kHz int16 mono played on the
/// audio owner's engine, JSON text frames (RTVI messages, our floor messages, `interruption`).
/// The microphone is never opened here: the audio owner stays its only owner.
@MainActor
final class WebSocketTalkClient: NSObject, TalkClient, URLSessionWebSocketDelegate {
    let kind: TalkTransportKind = .websocket
    private(set) var state: TalkConnectionState = .disconnected(nil)
    var onEvent: ((TalkEvent) -> Void)?
    var onState: ((TalkConnectionState) -> Void)?
    var onLog: ((String) -> Void)?
    private let engineVoice: EngineVoiceOutput
    var voice: VoiceOutput { engineVoice }
    private let uplink = PCMUplink()
    private weak var audio: AudioOwner?
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private var baseURL: URL?
    private var wantConnected = false
    private var backoff: Double = 1
    /// Bumped on every new socket; callbacks from an older socket are ignored.
    private var generation = 0
    private var pending: [TalkClientMessage] = []
    private var sessionId: String?
    private var connectedURL: String?

    init(audio: AudioOwner) {
        self.audio = audio
        engineVoice = EngineVoiceOutput(audio: audio)
        super.init()
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 15
        let queue = OperationQueue()
        queue.name = "rlmadk.talk.websocket"
        queue.maxConcurrentOperationCount = 1
        session = URLSession(configuration: config, delegate: self, delegateQueue: queue)
    }

    var detail: String {
        let counters = uplink.counters
        let down = engineVoice.received
        return "\(connectedURL ?? "-") session \(sessionId?.prefix(8) ?? "-") · up \(counters.frames) frames + \(counters.silence) silence · down \(down.frames) frames \(down.bytes / 1024) KiB"
    }

    func connect(baseURL: URL) {
        self.baseURL = baseURL
        wantConnected = true
        guard task == nil, state != .connecting else { return }
        open()
    }

    func disconnect() {
        wantConnected = false
        generation += 1
        uplink.stop()
        audio?.setTalkTap(nil)
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        engineVoice.flush()
        setState(.disconnected(nil))
    }

    func send(_ message: TalkClientMessage) {
        guard state.isUp, let task else {
            enqueue(message)
            return
        }
        guard let data = try? JSONSerialization.data(withJSONObject: message.json), let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { [weak self] error in
            guard let error else { return }
            Task { @MainActor in self?.onLog?("talk ws send failed: \(error.localizedDescription)") }
        }
    }

    /// Keeps at most one pending mode (the latest) and every text or event, in order.
    private func enqueue(_ message: TalkClientMessage) {
        if case .clientMode = message { pending.removeAll { if case .clientMode = $0 { return true } else { return false } } }
        pending.append(message)
    }

    // MARK: connection

    private func open() {
        guard let baseURL else { return }
        generation += 1
        let mine = generation
        setState(.connecting)
        Task { [weak self] in
            guard let self else { return }
            do {
                let wsURL = try await self.startSession(baseURL: baseURL)
                guard mine == self.generation, self.wantConnected else { return }
                let socket = self.session.webSocketTask(with: wsURL)
                socket.maximumMessageSize = 4 * 1024 * 1024
                self.task = socket
                self.connectedURL = wsURL.absoluteString
                socket.resume()
                self.receive(socket, generation: mine)
            } catch {
                guard mine == self.generation else { return }
                self.failed("start: \(error.localizedDescription)")
            }
        }
    }

    /// `POST /start` and the WebSocket URL built from the returned path.
    private func startSession(baseURL: URL) async throws -> URL {
        var request = URLRequest(url: baseURL.appendingPathComponent("start"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["transport": "websocket"])
        let (data, response) = try await session.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw NSError(domain: "talk", code: http.statusCode, userInfo: [NSLocalizedDescriptionKey: "HTTP \(http.statusCode): \(String(data: data, encoding: .utf8)?.prefix(160) ?? "")"])
        }
        let object = (try JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        sessionId = object["sessionId"] as? String
        return try WebSocketTalkClient.socketURL(base: baseURL, wsUrl: object["wsUrl"] as? String, token: object["token"] as? String)
    }

    /// `ws(s)://<base host:port><path of wsUrl>[/<token>]`.
    nonisolated static func socketURL(base: URL, wsUrl: String?, token: String?) throws -> URL {
        guard let wsUrl, !wsUrl.isEmpty else { throw NSError(domain: "talk", code: 1, userInfo: [NSLocalizedDescriptionKey: "start response has no wsUrl"]) }
        var path = URLComponents(string: wsUrl)?.path ?? wsUrl
        if path.isEmpty { path = "/ws-client" }
        if !path.hasPrefix("/") { path = "/" + path }
        if let token, !token.isEmpty, !path.hasSuffix("/" + token) { path += "/" + token }
        var comps = URLComponents()
        comps.scheme = base.scheme == "https" ? "wss" : "ws"
        comps.host = base.host
        comps.port = base.port
        comps.path = path
        guard let url = comps.url else { throw NSError(domain: "talk", code: 2, userInfo: [NSLocalizedDescriptionKey: "bad socket URL from \(wsUrl)"]) }
        return url
    }

    private enum ReceiveOutcome {
        case events([TalkEvent])
        case failed(String)
    }

    private func receive(_ socket: URLSessionWebSocketTask, generation mine: Int) {
        WebSocketTalkClient.receiveLoop(socket, voice: engineVoice) { [weak self] outcome in
            Task { @MainActor in
                guard let self, mine == self.generation else { return }
                switch outcome {
                case .events(let events): for event in events { self.onEvent?(event) }
                case .failed(let reason): self.failed(reason)
                }
            }
        }
    }

    /// Runs on the socket queue and re-arms itself there, so audio never waits for the main actor:
    /// bot PCM is decoded and scheduled in place; JSON events and failures hop to the main actor.
    nonisolated private static func receiveLoop(_ socket: URLSessionWebSocketTask, voice: EngineVoiceOutput, deliver: @escaping (ReceiveOutcome) -> Void) {
        socket.receive { result in
            switch result {
            case .failure(let error):
                deliver(.failed(error.localizedDescription))
                return
            case .success(.data(let data)):
                if let payload = PCMFraming.audioPayload(data) { voice.enqueue(pcm24k: payload) }
            case .success(.string(let text)):
                let events = TalkMessageParser.events(fromText: text)
                if events.contains(.interruption) { voice.flush() }
                if !events.isEmpty { deliver(.events(events)) }
            case .success:
                break
            }
            receiveLoop(socket, voice: voice, deliver: deliver)
        }
    }

    private func opened(generation mine: Int) {
        guard mine == generation else { return }
        backoff = 1
        setState(.connected)
        onLog?("talk websocket open: \(connectedURL ?? "?")")
        // Capture goes up from the audio owner's existing tap; silence while it delivers nothing.
        let socketRef = task
        uplink.start { frame in socketRef?.send(.data(frame)) { _ in } }
        let uplink = self.uplink
        audio?.setTalkTap { buffer in uplink.append(buffer) }
        let queued = pending
        pending.removeAll()
        for message in queued { send(message) }
    }

    private func failed(_ reason: String) {
        uplink.stop()
        audio?.setTalkTap(nil)
        task?.cancel()
        task = nil
        engineVoice.flush()
        setState(.disconnected(reason))
        onLog?("talk websocket down: \(reason)")
        guard wantConnected else { return }
        let delay = backoff
        backoff = min(backoff * 2, 15)
        let mine = generation
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            Task { @MainActor in
                guard let self, self.wantConnected, self.task == nil, mine == self.generation else { return }
                self.open()
            }
        }
    }

    private func setState(_ next: TalkConnectionState) {
        guard next != state else { return }
        state = next
        onState?(next)
    }

    // MARK: URLSessionWebSocketDelegate (socket queue)

    nonisolated func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol protocol: String?) {
        Task { @MainActor in
            guard webSocketTask === self.task else { return }
            self.opened(generation: self.generation)
        }
    }

    nonisolated func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        Task { @MainActor in
            guard webSocketTask === self.task else { return }
            self.failed("closed (\(closeCode.rawValue))")
        }
    }
}
