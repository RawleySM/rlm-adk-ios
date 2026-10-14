import AVFoundation
import Foundation

/// Launch-time audio knobs for the double-talk investigation of 2026-10-14: during a Scripture
/// reading the talk tier received no voice activity from the phone until the reading ended
/// (session websocket-d33b013b89), because the capture engine's voice processing attenuates the
/// near-end voice while the bot's playback is its echo reference. These knobs let a relaunch over
/// USB (`pymobiledevice3 developer dvt launch --env ...`) change the session mode, switch Apple's
/// voice processing or its automatic gain control off, or lower the bot's playback, so each
/// variant can be measured by the server's uplink probe without a rebuild. Values persist in
/// UserDefaults like the backend URL; an environment value overrides and replaces the stored one.
/// They take effect when the audio engine starts (at launch).
struct AudioTuning: Equatable {
    enum Mode: String, CaseIterable {
        case voiceChat
        case videoChat
        case standard = "default"

        var sessionMode: AVAudioSession.Mode {
            switch self {
            case .voiceChat: return .voiceChat
            case .videoChat: return .videoChat
            case .standard: return .default
            }
        }
    }

    /// `RLMADK_AUDIO_MODE`: voiceChat (the default), videoChat or default.
    var mode: Mode = .voiceChat
    /// `RLMADK_VOICE_PROCESSING`: 1 (default) keeps Apple's echo cancellation, noise suppression and
    /// gain control on the input node; 0 captures the raw microphone (the bot's own speech then
    /// reaches the uplink unless something else cancels it).
    var voiceProcessing = true
    /// `RLMADK_VP_AGC`: 1 (default) keeps voice processing's automatic gain control; 0 turns it off.
    var agc = true
    /// `RLMADK_BOT_GAIN_DB`: gain applied to the bot's playback on the capture engine (0 = unity).
    var botGainDb: Double = 0

    static let defaultsKey = "audioTuning"
    static let environmentKeys = ["RLMADK_AUDIO_MODE", "RLMADK_VOICE_PROCESSING", "RLMADK_VP_AGC", "RLMADK_BOT_GAIN_DB"]

    /// The tuning in force for this process: stored values, overridden and replaced by any
    /// environment values present at launch.
    static let current: AudioTuning = load(environment: ProcessInfo.processInfo.environment, defaults: .standard)

    static func load(environment: [String: String], defaults: UserDefaults?) -> AudioTuning {
        var tuning = AudioTuning()
        if let stored = defaults?.dictionary(forKey: defaultsKey) {
            tuning.apply(stored)
        }
        var fromEnvironment: [String: Any] = [:]
        if let mode = environment["RLMADK_AUDIO_MODE"] { fromEnvironment["mode"] = mode }
        if let vp = environment["RLMADK_VOICE_PROCESSING"] { fromEnvironment["voiceProcessing"] = vp }
        if let agc = environment["RLMADK_VP_AGC"] { fromEnvironment["agc"] = agc }
        if let gain = environment["RLMADK_BOT_GAIN_DB"] { fromEnvironment["botGainDb"] = gain }
        if !fromEnvironment.isEmpty {
            tuning.apply(fromEnvironment)
            defaults?.set(tuning.stored, forKey: defaultsKey)
        }
        return tuning
    }

    private static func flag(_ value: Any?) -> Bool? {
        if let b = value as? Bool { return b }
        guard let s = (value as? String)?.trimmingCharacters(in: .whitespaces).lowercased() else { return nil }
        if ["1", "true", "yes", "on"].contains(s) { return true }
        if ["0", "false", "no", "off"].contains(s) { return false }
        return nil
    }

    private mutating func apply(_ values: [String: Any]) {
        if let raw = values["mode"] as? String, let m = Mode(rawValue: raw) { mode = m }
        if let v = AudioTuning.flag(values["voiceProcessing"]) { voiceProcessing = v }
        if let v = AudioTuning.flag(values["agc"]) { agc = v }
        if let d = values["botGainDb"] as? Double { botGainDb = d } else if let s = values["botGainDb"] as? String, let d = Double(s) { botGainDb = d }
    }

    var stored: [String: Any] {
        ["mode": mode.rawValue, "voiceProcessing": voiceProcessing, "agc": agc, "botGainDb": botGainDb]
    }

    /// Linear gain for the bot's player node.
    var botGain: Float { Float(pow(10.0, botGainDb / 20.0)) }

    var description: String {
        "mode \(mode.rawValue) vp \(voiceProcessing ? "on" : "off") agc \(agc ? "on" : "off") botGain \(String(format: "%+.1f", botGainDb)) dB"
    }
}
