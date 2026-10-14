import Foundation

/// Which talk transport carries live audio to and from the Pipecat talk tier.
enum TalkTransportKind: String, CaseIterable, Codable, Equatable {
    /// SmallWebRTC through the official Pipecat Swift client (Opus both ways, RTVI data channel).
    case webrtc
    /// Our tagged-PCM WebSocket: the native audio owner's capture goes up as 16 kHz PCM, the bot's
    /// 24 kHz PCM plays through a player node on the same engine (no SDK).
    case websocket

    var label: String { self == .webrtc ? "WebRTC" : "WebSocket" }
}

/// Connection state of the active talk client.
enum TalkConnectionState: Equatable {
    case disconnected(String?)
    case connecting
    /// Transport is up (WebSocket open / WebRTC data channel open).
    case connected
    /// The bot reported ready (RTVI `bot-ready`); WebSocket sessions skip straight to connected.
    case ready

    var label: String {
        switch self {
        case .disconnected(let why): return why.map { "Disconnected: \($0)" } ?? "Disconnected"
        case .connecting: return "Connecting"
        case .connected: return "Connected"
        case .ready: return "Ready"
        }
    }

    var isUp: Bool { self == .connected || self == .ready }
}

/// Everything the phone hears from the talk tier, transport-independent. Both clients map their
/// wire messages (RTVI over the data channel or WebSocket text frames, our floor messages) into it.
enum TalkEvent: Equatable {
    case botReady
    /// The server's own transcript of the user (Cohere, per utterance).
    case userTranscript(text: String, final: Bool)
    case userStartedSpeaking
    case userStoppedSpeaking
    /// LLM text aggregated by sentence (`bot-transcription`), ahead of the audio. Display only.
    case botTranscript(text: String)
    /// Text that was actually spoken (`bot-tts-text`). Drives devotional position.
    case botSpokenText(text: String)
    case botStartedSpeaking
    case botStoppedSpeaking
    /// Flush playback now (`{"type":"interruption"}` or RTVI `bot-interrupted`).
    case interruption
    /// The server's floor changed (`{"type":"mode",...}`).
    case floorMode(mode: String, from: String?, reason: String?)
    /// Transcript text heard while praying (never sent to the LLM).
    case prayerText(text: String, final: Bool)
    case error(String)
}

/// Messages the phone sends to the talk tier.
enum TalkClientMessage: Equatable {
    case clientMode(ClientFloorMode, reason: String)
    case clientText(String)
    case clientEvent(name: String, detail: [String: String])

    /// The `type` value on the wire (WebSocket) and the RTVI client-message `t` (WebRTC).
    var type: String {
        switch self {
        case .clientMode: return "client_mode"
        case .clientText: return "client_text"
        case .clientEvent: return "client_event"
        }
    }

    /// Fields other than `type`.
    var fields: [String: String] {
        switch self {
        case .clientMode(let mode, let reason): return ["mode": mode.rawValue, "reason": reason]
        case .clientText(let text): return ["text": text]
        case .clientEvent(let name, let detail): return detail.merging(["event": name]) { _, new in new }
        }
    }

    /// The WebSocket text frame: `{"type": ..., ...fields}`.
    var json: [String: Any] {
        var out: [String: Any] = fields
        out["type"] = type
        return out
    }
}

/// Plays the bot audio of whichever talk transport is active, behind the phone's speech gate.
///
/// `SpeechGate` semantics: when `gateOpen` turns false (prayer or quiet) playback stops at once
/// and nothing plays until it opens again. `stop()` also silences the rest of the bot turn that is
/// already on its way (until the bot reports it stopped speaking or an interruption arrives).
protocol VoiceOutput: AnyObject {
    var gateOpen: Bool { get set }
    /// Bot audio is audible right now (scheduled and not yet played, or the transport says so).
    var isPlaying: Bool { get }
    /// Stop playback now and drop the rest of the current bot turn.
    func stop()
    /// Flush what is queued (server interruption); later audio of a new turn plays normally.
    func flush()
    /// The bot turn ended (bot-stopped-speaking): audio after this belongs to a new turn.
    func botTurnEnded()
    var onPlayingChange: ((Bool) -> Void)? { get set }
    /// One-line description for Diagnostics and the snapshot.
    var statusLine: String { get }
}

/// One talk transport. Main-actor API; implementations deliver events on the main actor.
@MainActor
protocol TalkClient: AnyObject {
    var kind: TalkTransportKind { get }
    var state: TalkConnectionState { get }
    var voice: VoiceOutput { get }
    var onEvent: ((TalkEvent) -> Void)? { get set }
    var onState: ((TalkConnectionState) -> Void)? { get set }
    /// Diagnostic lines (one per notable transport fact) for the app log.
    var onLog: ((String) -> Void)? { get set }
    /// Connects to `baseURL` (`POST {baseURL}/start`, then the transport) and keeps reconnecting
    /// with backoff until `disconnect()`.
    func connect(baseURL: URL)
    func disconnect()
    /// Sends now when up; otherwise kept and sent once the session is ready (latest mode wins).
    func send(_ message: TalkClientMessage)
    /// One-line transport detail for Diagnostics and the snapshot.
    var detail: String { get }
}

/// Maps wire JSON from the talk tier into `TalkEvent`s. Used directly by the WebSocket client and,
/// for server messages, by the WebRTC client.
enum TalkMessageParser {
    /// Args:
    ///   message: One decoded JSON object from the talk tier.
    ///
    /// Returns:
    ///   The events it carries (empty for metrics and anything unknown).
    static func events(from message: [String: Any]) -> [TalkEvent] {
        let type = message["type"] as? String ?? ""
        if message["label"] as? String == "rtvi-ai" {
            let data = message["data"] as? [String: Any] ?? [:]
            switch type {
            case "bot-ready": return [.botReady]
            case "user-transcription":
                guard let text = data["text"] as? String else { return [] }
                return [.userTranscript(text: text, final: data["final"] as? Bool ?? false)]
            case "user-started-speaking": return [.userStartedSpeaking]
            case "user-stopped-speaking": return [.userStoppedSpeaking]
            case "bot-transcription":
                return (data["text"] as? String).map { [.botTranscript(text: $0)] } ?? []
            case "bot-tts-text":
                return (data["text"] as? String).map { [.botSpokenText(text: $0)] } ?? []
            case "bot-started-speaking": return [.botStartedSpeaking]
            case "bot-stopped-speaking": return [.botStoppedSpeaking]
            case "bot-interrupted": return [.interruption]
            case "server-message":
                // Our floor messages as RTVI server messages (how they reach the WebRTC client).
                return events(from: data)
            case "error":
                return [.error((data["error"] as? String) ?? (data["message"] as? String) ?? "error")]
            default: return []
            }
        }
        switch type {
        case "interruption": return [.interruption]
        case "mode":
            guard let mode = message["mode"] as? String else { return [] }
            return [.floorMode(mode: mode, from: message["from"] as? String, reason: message["reason"] as? String)]
        case "prayer_text":
            guard let text = message["text"] as? String else { return [] }
            return [.prayerText(text: text, final: message["final"] as? Bool ?? true)]
        default:
            return []
        }
    }

    /// Parses one text frame.
    static func events(fromText text: String) -> [TalkEvent] {
        guard let data = text.data(using: .utf8), let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return [] }
        return events(from: object)
    }
}
