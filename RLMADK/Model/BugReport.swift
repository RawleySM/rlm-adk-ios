import Foundation

/// A talk-tier `bug_report` message: one step in the life of a report from the Bug chip.
struct BugReportEvent: Equatable {
    /// started | finishing | filing | filed | spooled | triaged | cancelled | none
    var state: String
    var reportId: String?
    var ticketId: String?
    var title: String?
    var segments: Int?
    var priority: String?
    var component: String?
    var disposition: String?
    var error: String?
    /// The triaged kind: bug, feature_request, message, teaching or project_proposal.
    var kind: String?

    init?(message: [String: Any]) {
        guard let state = message["state"] as? String else { return nil }
        self.init(state: state, reportId: message["report_id"] as? String, ticketId: message["ticket_id"] as? String, title: message["title"] as? String,
                  segments: message["segments"] as? Int, priority: message["priority"] as? String, component: message["component"] as? String,
                  disposition: message["disposition"] as? String, error: message["error"] as? String, kind: message["kind"] as? String)
    }

    init(state: String, reportId: String? = nil, ticketId: String? = nil, title: String? = nil, segments: Int? = nil, priority: String? = nil,
         component: String? = nil, disposition: String? = nil, error: String? = nil, kind: String? = nil) {
        self.state = state
        self.reportId = reportId
        self.ticketId = ticketId
        self.title = title
        self.segments = segments
        self.priority = priority
        self.component = component
        self.disposition = disposition
        self.error = error
        self.kind = kind
    }
}

/// The Bug chip on the phone.
///
/// Tapping it starts a report at once ("Bug Reporting..."): the phone closes its own speech gate
/// and the talk tier stops answering, while the microphone and both transcribers keep running.
/// Every final transcript the server hears comes back as a line of the report. The second tap
/// files it (the talk tier posts it to the desktop's intake issues log); the chip shows the ticket
/// and, a little later, the triaged title and priority, then Intake's replies about the ticket.
struct BugReportState: Equatable {
    enum Phase: Equatable {
        case idle, reporting, filing, filed, spooled, failed
    }

    var phase: Phase = .idle
    var reportId: String?
    var startedAt: Date?
    /// Final server transcript lines (voice or typed), in order.
    var lines: [String] = []
    /// The server's interim text of the utterance in progress.
    var interim: String = ""
    /// The phone's own recognizer finals during the report, sent with the stop.
    var deviceLines: [String] = []
    var ticketId: String?
    var title: String?
    /// "P2 · scripture pane · ready" ("teaching · P3 · other · request" for a kind other than a bug),
    /// or why it was not filed.
    var detail: String?
    /// Intake's latest replies about this ticket (a reply on it, or mail naming it), newest last.
    var replies: [IntakeMessage] = []
    static let maxReplies = 3

    /// True while the report is open or being filed: the phone keeps bot speech silent.
    var silencesOutput: Bool { phase == .reporting || phase == .filing }

    var chipLabel: String {
        switch phase {
        case .reporting: return "Bug Reporting..."
        case .filing: return "Filing…"
        default: return "Bug"
        }
    }

    /// One status line under the strip once a report has ended (nil while idle or reporting).
    var outcome: String? {
        switch phase {
        case .filed:
            let head = "Filed \(ticketId ?? "bug report")"
            return [head + (title.map { ": \($0)" } ?? ""), detail].compactMap { $0 }.joined(separator: " — ")
        case .spooled: return "Bug report saved on the talk server; it is filed when the desktop answers."
        case .failed: return "Bug report not filed: \(detail ?? "unknown")"
        default: return nil
        }
    }

    /// The tap that opens a report: shown at once, before the server answers.
    mutating func begin(at now: Date = Date()) {
        self = BugReportState(phase: .reporting, startedAt: now)
    }

    /// Applies a talk-tier `bug_report` message.
    mutating func apply(_ event: BugReportEvent) {
        if let id = event.reportId, let mine = reportId, id != mine, event.state != "started" { return } // another report's late message
        switch event.state {
        case "started":
            if phase != .filing { phase = .reporting }
            reportId = event.reportId ?? reportId
        case "finishing", "filing":
            phase = .filing
        case "filed":
            phase = .filed
            ticketId = event.ticketId
            title = event.title
        case "spooled":
            phase = .spooled
            detail = event.error
        case "triaged":
            guard event.ticketId == nil || event.ticketId == ticketId else { return }
            title = event.title ?? title
            let kind = event.kind.flatMap { $0 == "bug" ? nil : $0.replacingOccurrences(of: "_", with: " ") }
            detail = [kind, event.priority, event.component?.replacingOccurrences(of: "_", with: " "), event.disposition?.replacingOccurrences(of: "_", with: " ")].compactMap { $0 }.joined(separator: " · ")
        case "cancelled":
            self = BugReportState()
        case "none":
            if silencesOutput {
                phase = .failed
                detail = "the talk session had no open report (it restarted); say it again"
            }
        default:
            break
        }
    }

    /// A transcript line from the server (`bug_text`).
    mutating func heard(_ text: String, final: Bool) {
        guard silencesOutput else { return }
        if final {
            let line = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !line.isEmpty { lines.append(line) }
            interim = ""
        } else {
            interim = text
        }
    }

    /// A message from Intake: kept when it is about this report's ticket (each once, the last few).
    mutating func received(_ message: IntakeMessage) {
        guard let ticketId, message.concerns(ticketId), !replies.contains(where: { $0.id == message.id }) else { return }
        replies.append(message)
        if replies.count > Self.maxReplies { replies.removeFirst(replies.count - Self.maxReplies) }
    }

    /// The talk connection dropped mid-report; the server files what it heard when its session ends.
    mutating func connectionLost() {
        guard silencesOutput else { return }
        phase = .failed
        detail = "the connection dropped; the talk server files what it heard"
    }
}
