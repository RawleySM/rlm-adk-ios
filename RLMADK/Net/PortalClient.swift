import Foundation

/// Text/control WebSocket plus the HTTP routes of the voice portal backend.
final class PortalClient: NSObject, URLSessionWebSocketDelegate {
    enum Connection: Equatable {
        case disconnected(String?)
        case connecting
        case connected(sessionId: String)

        var label: String {
            switch self {
            case .disconnected(let why): return why.map { "Disconnected: \($0)" } ?? "Disconnected"
            case .connecting: return "Connecting"
            case .connected(let id): return "Connected (\(id.prefix(8)))"
            }
        }

        var isConnected: Bool { if case .connected = self { return true } else { return false } }
    }

    private(set) var baseURL: URL
    private var session: URLSession!
    private var task: URLSessionWebSocketTask?
    private(set) var connection: Connection = .disconnected(nil)
    var onConnection: ((Connection) -> Void)?
    var onEvent: ((ServerEvent, [String: Any]) -> Void)?
    private var wantConnected = false
    private var backoff: Double = 1

    init(baseURL: URL, configuration: URLSessionConfiguration = .default) {
        self.baseURL = baseURL
        super.init()
        let config = configuration
        config.waitsForConnectivity = false
        config.timeoutIntervalForRequest = 30
        session = URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }

    func setBaseURL(_ url: URL) {
        baseURL = url
        if wantConnected {
            disconnect()
            connect()
        }
    }

    var wsURL: URL {
        var comps = URLComponents(url: baseURL.appendingPathComponent("ws"), resolvingAgainstBaseURL: false)!
        comps.scheme = baseURL.scheme == "https" ? "wss" : "ws"
        return comps.url!
    }

    func connect() {
        wantConnected = true
        guard task == nil else { return }
        setConnection(.connecting)
        let t = session.webSocketTask(with: wsURL)
        task = t
        t.resume()
        receiveLoop(t)
    }

    func disconnect() {
        wantConnected = false
        task?.cancel(with: .normalClosure, reason: nil)
        task = nil
        setConnection(.disconnected(nil))
    }

    func send(_ payload: [String: Any]) {
        guard let task, let data = try? JSONSerialization.data(withJSONObject: payload), let text = String(data: data, encoding: .utf8) else { return }
        task.send(.string(text)) { [weak self] error in
            if let error { self?.handleFailure("send: \(error.localizedDescription)") }
        }
    }

    private func receiveLoop(_ t: URLSessionWebSocketTask) {
        t.receive { [weak self] result in
            guard let self, self.task === t else { return }
            switch result {
            case .failure(let error):
                self.handleFailure(error.localizedDescription)
            case .success(let message):
                if case .string(let text) = message, let data = text.data(using: .utf8),
                   let raw = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
                   let event = try? JSONDecoder().decode(ServerEvent.self, from: data) {
                    if event.type == "session", let id = event.session_id {
                        self.backoff = 1
                        self.setConnection(.connected(sessionId: id))
                    }
                    DispatchQueue.main.async { self.onEvent?(event, raw) }
                }
                self.receiveLoop(t)
            }
        }
    }

    private func handleFailure(_ reason: String) {
        task?.cancel()
        task = nil
        setConnection(.disconnected(reason))
        guard wantConnected else { return }
        let delay = backoff
        backoff = min(backoff * 2, 15)
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.wantConnected, self.task == nil else { return }
            self.connect()
        }
    }

    private func setConnection(_ c: Connection) {
        connection = c
        DispatchQueue.main.async { self.onConnection?(c) }
    }

    func urlSession(_ session: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith closeCode: URLSessionWebSocketTask.CloseCode, reason: Data?) {
        if task === webSocketTask { handleFailure("closed (\(closeCode.rawValue))") }
    }

    // MARK: HTTP

    struct HTTPError: Error, LocalizedError {
        var status: Int
        var body: String
        var errorDescription: String? { "HTTP \(status): \(body.prefix(200))" }
    }

    func get<T: Decodable>(_ path: String, query: [String: String] = [:], as type: T.Type) async throws -> T {
        var comps = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { comps.queryItems = query.map { URLQueryItem(name: $0.key, value: $0.value) } }
        let (data, response) = try await session.data(from: comps.url!)
        try Self.check(response, data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    func post<T: Decodable>(_ path: String, json: [String: Any], as type: T.Type) async throws -> T {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: json)
        let (data, response) = try await session.data(for: request)
        try Self.check(response, data)
        return try JSONDecoder().decode(T.self, from: data)
    }

    func postRaw(_ path: String, body: Data, headers: [String: String]) async throws -> Data {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = "POST"
        request.timeoutInterval = 300
        for (k, v) in headers { request.setValue(v, forHTTPHeaderField: k) }
        let (data, response) = try await session.upload(for: request, from: body)
        try Self.check(response, data)
        return data
    }

    func getRaw(_ path: String) async throws -> Data {
        let (data, response) = try await session.data(from: baseURL.appendingPathComponent(path))
        try Self.check(response, data)
        return data
    }

    private static func check(_ response: URLResponse, _ data: Data) throws {
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw HTTPError(status: http.statusCode, body: String(data: data, encoding: .utf8) ?? "")
        }
    }
}
