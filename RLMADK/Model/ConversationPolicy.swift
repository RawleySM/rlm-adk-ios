import Foundation

/// The conversation floor policy the phone shares with the talk tier: address phrases that open a
/// prayer, amen phrases that close it after a silence window, quiet and wake phrases.
///
/// Defaults mirror the talk server's `TalkConfig` (`~/srv/voice-talk/talk/config.py`). The skill
/// service's `GET /policy` overrides any subset of them; the app fetches it at launch and caches the
/// last good body in `Documents/policy.json`, so a launch without the backend keeps the last policy.
struct ConversationPolicy: Codable, Equatable {
    var addressPhrases: [String]
    var amenPhrases: [String]
    /// Seconds with no new words after an amen before the prayer ends (the amen rule).
    var amenSilenceSeconds: Double
    var quietPhrases: [String]
    var wakePhrases: [String]
    /// Exact-match commands that begin a prayer (the spoken twin of the Begin prayer button).
    var prayerStartCommands: [String]
    /// Suffix commands that end a prayer at once, without the amen silence window.
    var prayerEndCommands: [String]
    /// Audio kept from just before an address-triggered prayer so its first words are in the capture.
    var prayerPrerollSeconds: Double
    /// Pre-roll when the talk server (not the phone) detected the address after the utterance ended.
    var serverPrayerPrerollSeconds: Double

    static let defaults = ConversationPolicy(
        addressPhrases: [
            "lord", "dear lord", "lord jesus", "father", "dear father", "heavenly father", "our father",
            "dear god", "god,", "jesus", "dear jesus", "holy spirit", "abba", "let's pray", "let us pray",
            "i pray", "we pray",
        ],
        amenPhrases: ["amen", "in jesus' name amen", "in jesus name amen"],
        amenSilenceSeconds: 1.2,
        quietPhrases: ["quiet please", "be quiet", "go quiet"],
        wakePhrases: ["wake up", "portal wake"],
        prayerStartCommands: ["begin prayer"],
        prayerEndCommands: ["end prayer", "amen end prayer"],
        prayerPrerollSeconds: 2.0,
        serverPrayerPrerollSeconds: 6.0
    )

    enum CodingKeys: String, CodingKey {
        case addressPhrases = "address_phrases"
        case amenPhrases = "amen_phrases"
        case amenSilenceSeconds = "amen_silence_s"
        case quietPhrases = "quiet_phrases"
        case wakePhrases = "wake_phrases"
        case prayerStartCommands = "prayer_start_commands"
        case prayerEndCommands = "prayer_end_commands"
        case prayerPrerollSeconds = "prayer_preroll_s"
        case serverPrayerPrerollSeconds = "server_prayer_preroll_s"
    }

    /// Applies a `GET /policy` body over `base`.
    ///
    /// The body may be the policy object itself or wrap it under `"policy"`. Any subset of keys may
    /// be present; a missing, empty or out-of-range value keeps the base value, so a partial or
    /// newer server policy can never blank a phrase table on the phone.
    ///
    /// Args:
    ///   data: The raw response body.
    ///   base: The policy to start from (defaults, or the cached policy).
    ///
    /// Returns:
    ///   The merged policy, or nil when the body is not a JSON object.
    static func merged(from data: Data, over base: ConversationPolicy = .defaults) -> ConversationPolicy? {
        guard let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return nil }
        let body = (object["policy"] as? [String: Any]) ?? object
        var policy = base
        func phrases(_ key: CodingKeys) -> [String]? {
            guard let list = body[key.rawValue] as? [Any] else { return nil }
            let cleaned = list.compactMap { ($0 as? String)?.trimmingCharacters(in: .whitespaces).lowercased() }.filter { !$0.isEmpty }
            return cleaned.isEmpty ? nil : cleaned
        }
        func seconds(_ key: CodingKeys, _ range: ClosedRange<Double>) -> Double? {
            guard let value = (body[key.rawValue] as? NSNumber)?.doubleValue, range.contains(value) else { return nil }
            return value
        }
        if let v = phrases(.addressPhrases) { policy.addressPhrases = v }
        if let v = phrases(.amenPhrases) { policy.amenPhrases = v }
        if let v = phrases(.quietPhrases) { policy.quietPhrases = v }
        if let v = phrases(.wakePhrases) { policy.wakePhrases = v }
        if let v = phrases(.prayerStartCommands) { policy.prayerStartCommands = v }
        if let v = phrases(.prayerEndCommands) { policy.prayerEndCommands = v }
        if let v = seconds(.amenSilenceSeconds, 0.3...10) { policy.amenSilenceSeconds = v }
        if let v = seconds(.prayerPrerollSeconds, 0...8) { policy.prayerPrerollSeconds = v }
        if let v = seconds(.serverPrayerPrerollSeconds, 0...8) { policy.serverPrayerPrerollSeconds = v }
        return policy
    }

    /// The address phrase an utterance starts with, or nil.
    ///
    /// Phrases written with punctuation (`"god,"`) match only when the transcript carries that
    /// punctuation right after the word, so "God, thank you" opens a prayer and "God is good" does
    /// not. Other phrases match whole leading words: "Lord" and "Lord, hear us" match `lord`;
    /// "Lordship" does not.
    func address(startOf text: String) -> String? {
        let plain = FloorText.normalize(text)
        let commas = FloorText.normalizeKeepingCommas(text)
        for phrase in addressPhrases.sorted(by: { $0.count > $1.count }) {
            if phrase.contains(",") {
                let wanted = FloorText.normalizeKeepingCommas(phrase)
                if !wanted.isEmpty, commas.hasPrefix(wanted) { return phrase }
            } else {
                let wanted = FloorText.normalize(phrase)
                if !wanted.isEmpty, plain == wanted || plain.hasPrefix(wanted + " ") { return phrase }
            }
        }
        return nil
    }

    /// True when the transcript ends with an amen phrase ("... amen", "in Jesus' name, amen.").
    func endsWithAmen(_ text: String) -> Bool {
        let plain = FloorText.normalize(text)
        return amenPhrases.map(FloorText.normalize).contains { !$0.isEmpty && (plain == $0 || plain.hasSuffix(" " + $0)) }
    }

    func isQuietCommand(_ text: String) -> Bool {
        let plain = FloorText.normalize(text)
        return quietPhrases.map(FloorText.normalize).contains { plain == $0 || plain.hasSuffix(" " + $0) }
    }

    func isWakeCommand(_ text: String) -> Bool {
        let plain = FloorText.normalize(text)
        return wakePhrases.map(FloorText.normalize).contains { plain == $0 || plain.hasPrefix($0 + " ") }
    }

    func isPrayerStartCommand(_ text: String) -> Bool {
        let plain = FloorText.normalize(text)
        return prayerStartCommands.map(FloorText.normalize).contains { plain == $0 }
    }

    func isPrayerEndCommand(_ text: String) -> Bool {
        let plain = FloorText.normalize(text)
        return prayerEndCommands.map(FloorText.normalize).contains { plain == $0 || plain.hasSuffix(" " + $0) }
    }
}

/// Transcript normalisation shared by every floor rule, matching the talk server's `_norm`
/// (lowercase, letters and apostrophes, everything else a space) with whitespace collapsed and
/// typographic apostrophes folded, because the on-device recognizer writes "let’s" and "Jesus’".
enum FloorText {
    static func normalize(_ text: String) -> String { fold(text, keepCommas: false) }

    static func normalizeKeepingCommas(_ text: String) -> String { fold(text, keepCommas: true) }

    static func words(_ text: String) -> [String] { normalize(text).split(separator: " ").map(String.init) }

    private static func fold(_ text: String, keepCommas: Bool) -> String {
        var out = ""
        var pendingSpace = false
        for ch in text.lowercased() {
            let c: Character = (ch == "\u{2019}" || ch == "\u{2018}") ? "'" : ch
            if ("a"..."z").contains(c) || c == "'" {
                if pendingSpace, !out.isEmpty, out.last != " " { out.append(" ") }
                pendingSpace = false
                out.append(c)
            } else if keepCommas, c == "," {
                // Attach the comma to the word before it ("god,"), then continue with a space.
                if out.last == " " { out.removeLast() }
                if !out.isEmpty { out.append(",") }
                pendingSpace = true
            } else {
                pendingSpace = true
            }
        }
        return out.trimmingCharacters(in: .whitespaces)
    }
}

/// Facts about voice in the microphone signal, from the audio owner's level tracker. Used only to
/// anchor the amen silence window to the end of speech rather than to the arrival of the partial.
struct VoiceFacts: Equatable {
    /// Voice energy is present right now.
    var active: Bool
    /// When voice energy last ended (nil if never seen).
    var lastVoiceEnd: Date?
}

/// Phone-side prayer floor from on-device transcripts, decided before the server hears the
/// utterance. Pure and clocked by the caller, so every rule is testable without audio.
///
/// Rules (the acceptance matrix, case 1):
/// - Not praying: an utterance whose text starts with an address phrase enters prayer at once,
///   on the first partial that shows it.
/// - Praying: a partial or final that ends with an amen phrase starts the amen window. The prayer
///   ends when `amenSilenceSeconds` pass with no new words, counted from the later of the moment
///   the amen words appeared and (when the level tracker saw it) the end of voice energy after them
///   — whichever reaches the window first. More words cancel the pending end.
/// - Silence alone never ends a prayer: without an amen, `tick` never decides anything.
struct PrayerFloor: Equatable {
    enum Decision: Equatable {
        case none
        case enterPrayer(address: String)
        case amenPending
        case amenCancelled
        case endPrayer
    }

    /// Longest plausible delay between speaking a word and its partial arriving.
    static let maxPartialLatency: TimeInterval = 1.0

    private(set) var policy: ConversationPolicy
    /// Words of the current utterance as last seen (empty after a final or an utterance boundary).
    private(set) var lastWords: [String] = []
    /// When the words that end in amen appeared; nil when no amen is pending.
    private(set) var amenHeardAt: Date?

    init(policy: ConversationPolicy = .defaults) { self.policy = policy }

    mutating func setPolicy(_ policy: ConversationPolicy) { self.policy = policy }

    /// A new recognition request started: the next partial is a new utterance. A pending amen stays.
    mutating func newUtterance() { lastWords = [] }

    /// Forget everything (prayer ended or began).
    mutating func reset() {
        lastWords = []
        amenHeardAt = nil
    }

    /// One transcript of the current utterance.
    ///
    /// Args:
    ///   text: Partial or final transcript text.
    ///   final: True for a final (closes the utterance).
    ///   praying: Whether the phone is praying now.
    ///   now: The observation time.
    ///
    /// Returns:
    ///   What the caller must do.
    mutating func observe(text: String, final: Bool, praying: Bool, at now: Date) -> Decision {
        let words = FloorText.words(text)
        let grew = words.count > lastWords.count || (!words.isEmpty && words.last != lastWords.last)
        lastWords = final ? [] : words
        guard !words.isEmpty else { return .none }
        if !praying {
            amenHeardAt = nil
            if let address = policy.address(startOf: text) { return .enterPrayer(address: address) }
            return .none
        }
        // Repeats and revisions of earlier words are not new words: the pending end stands.
        guard grew else { return .none }
        if policy.endsWithAmen(text) {
            amenHeardAt = now
            return .amenPending
        }
        if amenHeardAt != nil {
            amenHeardAt = nil
            return .amenCancelled
        }
        return .none
    }

    /// Periodic check while praying.
    ///
    /// Args:
    ///   praying: Whether the phone is praying now.
    ///   now: The tick time.
    ///   voice: Level-tracker facts, or nil when unavailable (then only the word rule applies).
    ///
    /// Returns:
    ///   `.endPrayer` once the amen window has passed with no new words; otherwise `.none`.
    mutating func tick(praying: Bool, at now: Date, voice: VoiceFacts? = nil) -> Decision {
        guard praying, let heard = amenHeardAt else { return .none }
        let window = policy.amenSilenceSeconds
        var ended = now.timeIntervalSince(heard) >= window
        if !ended, let voice, !voice.active, let voiceEnd = voice.lastVoiceEnd,
           voiceEnd >= heard.addingTimeInterval(-PrayerFloor.maxPartialLatency) {
            // NOTE [Context]: the partial carrying "amen" arrives some hundreds of milliseconds after
            // the word was spoken. When the level tracker saw speech end within that latency of the
            // partial (or after it), the window counts from that end, as the server's VAD-anchored
            // rule does. An older voice end is not this utterance's end and is ignored, so a level
            // tracker that missed the speech can never end a prayer early; it can only make the
            // word rule's end come sooner, never later.
            ended = now.timeIntervalSince(voiceEnd) >= window
        }
        guard ended else { return .none }
        amenHeardAt = nil
        return .endPrayer
    }
}

/// Fetches `GET /policy` from the skill service and caches the last good body.
enum PolicyStore {
    static func cacheURL(root: URL) -> URL { root.appendingPathComponent("policy.json") }

    /// The cached policy (merged over defaults), or the defaults.
    static func load(root: URL) -> (policy: ConversationPolicy, source: String) {
        let url = cacheURL(root: root)
        guard let data = try? Data(contentsOf: url), let policy = ConversationPolicy.merged(from: data) else {
            return (.defaults, "defaults")
        }
        let stamp = (try? FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate] as? Date).map { ISO8601DateFormatter().string(from: $0) } ?? "?"
        return (policy, "cached \(stamp)")
    }

    /// Fetches and caches the policy. Returns nil (cache untouched) when the service has none.
    static func fetch(client: PortalClient, root: URL) async -> (policy: ConversationPolicy, source: String)? {
        guard let data = try? await client.getRaw("policy"), let policy = ConversationPolicy.merged(from: data) else { return nil }
        try? data.write(to: cacheURL(root: root), options: .atomic)
        return (policy, "fetched \(ISO8601DateFormatter().string(from: Date()))")
    }
}
