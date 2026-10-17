import Foundation

/// A message from the Intake resident, as the skill service pushes it on the phone's control
/// WebSocket (`{"type": "intake_message", ...}`) or serves it from `GET /intake/feed`: Intake's
/// reply on a ticket (`kind` "reply") or mail it sent without replying to one ("inbound").
/// The conversation pane shows the newest as an "Intake: ..." line; a reply about the ticket on
/// the Bug card is listed there too.
struct IntakeMessage: Codable, Equatable, Identifiable {
    /// The mailbox message id (`mail-...`).
    var id: String
    /// "reply" (threaded on a ticket's mail) or "inbound".
    var kind: String
    var ticket_id: String?
    var ticket_title: String?
    /// The reply's leading word: accepted, answered, learned, proposed, started, done, failed,
    /// needs_detail, declined, escalated; nil for inbound mail without one.
    var status: String?
    /// The message as Intake sent it.
    var text: String
    /// The words without the leading "status: <word>", as the pane shows them.
    var display: String?
    /// The sender's name in the mailbox ("Intake").
    var from_name: String?
    var sent_at: String?
    /// When the skill service recorded it (Unix seconds): the feed's cursor.
    var at: Double?
    /// Ticket ids that inbound mail names in its text.
    var mentions: [String]?

    /// Who the pane names before the words: the sender, else "Intake".
    var speaker: String { from_name.flatMap { $0.isEmpty ? nil : $0 } ?? "Intake" }

    /// The words after "Intake:".
    var shownText: String { display.flatMap { $0.isEmpty ? nil : $0 } ?? text }

    /// The line under it: "answered · bug-20261017-02".
    var detail: String {
        [status?.replacingOccurrences(of: "_", with: " "), ticket_id].compactMap { $0 }.joined(separator: " · ")
    }

    /// True when it is about `ticket`: a reply on it, or mail that names it.
    func concerns(_ ticket: String) -> Bool { ticket_id == ticket || (mentions ?? []).contains(ticket) }
}

extension IntakeMessage {
    /// Decodes one pushed frame or feed item (extra keys such as `type` are ignored).
    ///
    /// Returns nil unless it carries an id and text.
    init?(message: [String: Any]) {
        guard JSONSerialization.isValidJSONObject(message),
              let data = try? JSONSerialization.data(withJSONObject: message),
              let decoded = try? JSONDecoder().decode(IntakeMessage.self, from: data),
              !decoded.id.isEmpty else { return nil }
        self = decoded
    }

    private static let latestKey = "intakeLatest"

    /// The newest message applied on this phone, kept across launches so the line survives a relaunch.
    static func loadLatest(_ defaults: UserDefaults = .standard) -> IntakeMessage? {
        defaults.data(forKey: latestKey).flatMap { try? JSONDecoder().decode(IntakeMessage.self, from: $0) }
    }

    func saveAsLatest(_ defaults: UserDefaults = .standard) {
        if let data = try? JSONEncoder().encode(self) { defaults.set(data, forKey: Self.latestKey) }
    }
}

/// `GET /intake/feed`: Intake's replies and mail recorded after a cursor, newest last.
struct IntakeFeed: Decodable {
    var items: [IntakeMessage]
}
