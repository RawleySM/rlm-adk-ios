import XCTest
@testable import RLMADK

/// Intake's replies and mail on the phone: the skill service's `intake_message` frame (the same
/// shape `GET /intake/feed` serves), the conversation pane's "Intake:" line, the replies on the
/// Bug card, and the triaged kind on the card's detail line.
@MainActor
final class IntakeMessageTests: XCTestCase {
    /// `voice_portal.issues.feed_item` output for a threaded reply, verbatim.
    static let replyFrame = #"{"at": 1792224000.5, "display": "A pause after each verse when reading the Psalms; intake_intk01/v3 holds it.", "from_name": "Intake", "id": "mail-5b1e", "in_reply_to": "mail-77", "instance_id": "intake-solo-20261017", "kind": "reply", "mentions": [], "sent_at": "2026-10-17T09:00:00+00:00", "status": "learned", "text": "status: learned. A pause after each verse when reading the Psalms; intake_intk01/v3 holds it.", "ticket_id": "bug-20261017-02", "ticket_kind": "teaching", "ticket_title": "Pause after each verse in the Psalms", "type": "intake_message"}"#

    private var roots: [URL] = []

    override func setUp() {
        super.setUp()
        UserDefaults.standard.removeObject(forKey: "intakeLatest")
        UserDefaults.standard.removeObject(forKey: "intakeFeedSince")
    }

    override func tearDown() {
        for root in roots { try? FileManager.default.removeItem(at: root) }
        roots = []
        UserDefaults.standard.removeObject(forKey: "intakeLatest")
        UserDefaults.standard.removeObject(forKey: "intakeFeedSince")
        super.tearDown()
    }

    private func object(_ json: String) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] ?? [:]
    }

    private func message(_ id: String, ticket: String?, status: String? = "accepted", text: String = "status: accepted. On it.", at: Double, mentions: [String]? = nil) -> IntakeMessage {
        IntakeMessage(id: id, kind: ticket == nil ? "inbound" : "reply", ticket_id: ticket, ticket_title: nil, status: status, text: text,
                      display: nil, from_name: "Intake", sent_at: nil, at: at, mentions: mentions)
    }

    private func makeModel() -> (PortalModel, FakeTalkClient) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("intake-\(UUID().uuidString)")
        roots.append(root)
        let model = PortalModel(store: LocalStore(root: root))
        model.recognitionStarter = {}
        model.deliveryProbe = { true }
        var made: FakeTalkClient?
        model.talkFactory = { kind in
            let fake = FakeTalkClient(kind: kind)
            made = fake
            return fake
        }
        model.connectTalk()
        return (model, made!)
    }

    func testTheSkillServicesFrameParses() throws {
        let parsed = try XCTUnwrap(IntakeMessage(message: object(Self.replyFrame)))
        XCTAssertEqual(parsed.id, "mail-5b1e")
        XCTAssertEqual(parsed.kind, "reply")
        XCTAssertEqual(parsed.ticket_id, "bug-20261017-02")
        XCTAssertEqual(parsed.status, "learned")
        XCTAssertEqual(parsed.at, 1792224000.5)
        XCTAssertEqual(parsed.speaker, "Intake")
        XCTAssertEqual(parsed.shownText, "A pause after each verse when reading the Psalms; intake_intk01/v3 holds it.")
        XCTAssertEqual(parsed.detail, "learned · bug-20261017-02")
        XCTAssertTrue(parsed.concerns("bug-20261017-02"))
        XCTAssertFalse(parsed.concerns("bug-20261017-01"))
    }

    func testTheFrameSurvivesTheControlSocketsDecode() throws {
        // PortalClient drops a frame that does not decode as a ServerEvent before the model sees it.
        let event = try JSONDecoder().decode(ServerEvent.self, from: Data(Self.replyFrame.utf8))
        XCTAssertEqual(event.type, "intake_message")
    }

    func testInboundMailAndBadFrames() throws {
        let inbound = try XCTUnwrap(IntakeMessage(message: object(#"{"type": "intake_message", "id": "mail-9", "kind": "inbound", "ticket_id": null, "status": null, "text": "Intake is running and has your four open tickets.", "display": "Intake is running and has your four open tickets.", "from_name": null, "at": 1792224100, "mentions": ["bug-20261016-06"]}"#)))
        XCTAssertNil(inbound.ticket_id)
        XCTAssertEqual(inbound.speaker, "Intake", "an unnamed sender is Intake")
        XCTAssertEqual(inbound.detail, "")
        XCTAssertTrue(inbound.concerns("bug-20261016-06"), "mail that names a ticket concerns it")
        XCTAssertNil(IntakeMessage(message: ["type": "intake_message", "text": "no id"]))
        XCTAssertNil(IntakeMessage(message: ["type": "intake_message", "id": "", "kind": "reply", "text": "empty id"]))
        let plain = try XCTUnwrap(IntakeMessage(message: ["id": "m1", "kind": "reply", "text": "Done. Build 28 is on the page."]))
        XCTAssertEqual(plain.shownText, "Done. Build 28 is on the page.", "no display: the text as sent")
    }

    func testTheNewestMessageIsTheConversationLineAndPersists() throws {
        let (model, _) = makeModel()
        XCTAssertNil(model.intakeLatest)
        model.intakeMessageReceived(try XCTUnwrap(IntakeMessage(message: object(Self.replyFrame))))
        XCTAssertEqual(model.intakeLatest?.shownText, "A pause after each verse when reading the Psalms; intake_intk01/v3 holds it.")
        model.intakeMessageReceived(message("mail-older", ticket: "bug-20261016-06", at: 1792220000))
        XCTAssertEqual(model.intakeLatest?.id, "mail-5b1e", "an older message never replaces the newer line")
        model.intakeMessageReceived(message("mail-newer", ticket: nil, status: nil, text: "Build 28 is published.", at: 1792230000))
        XCTAssertEqual(model.intakeLatest?.id, "mail-newer")
        XCTAssertEqual(UserDefaults.standard.double(forKey: "intakeFeedSince"), 1792230000, "the feed resumes after the newest")
        XCTAssertEqual(IntakeMessage.loadLatest()?.id, "mail-newer", "the line survives a relaunch")
        let (relaunched, _) = makeModel()
        XCTAssertEqual(relaunched.intakeLatest?.text, "Build 28 is published.")
    }

    func testRepliesAboutTheFiledTicketJoinTheBugCard() {
        let (model, _) = makeModel()
        model.startBugReport()
        model.finishBugReport()
        model.talkEventReceived(.bugReport(BugReportEvent(state: "filed", ticketId: "bug-20261017-03", title: "Shading lags")))
        model.intakeMessageReceived(message("mail-a", ticket: "bug-20261017-03", at: 1))
        model.intakeMessageReceived(message("mail-a", ticket: "bug-20261017-03", at: 1))
        model.intakeMessageReceived(message("mail-b", ticket: "bug-20261016-01", at: 2))
        model.intakeMessageReceived(message("mail-c", ticket: nil, status: "done", text: "status: done. bug-20261017-03 is in build 28.", at: 3, mentions: ["bug-20261017-03"]))
        XCTAssertEqual(model.bug.replies.map(\.id), ["mail-a", "mail-c"], "each once; another ticket's reply stays off the card")
        XCTAssertEqual(model.intakeLatest?.id, "mail-c", "every message still reaches the conversation line")
        model.dismissBugOutcome()
        XCTAssertTrue(model.bug.replies.isEmpty)
    }

    func testTheCardKeepsTheLatestThreeReplies() {
        var bug = BugReportState()
        bug.begin()
        bug.apply(BugReportEvent(state: "filed", ticketId: "bug-1"))
        for n in 1...5 { bug.received(message("m\(n)", ticket: "bug-1", at: Double(n))) }
        XCTAssertEqual(bug.replies.map(\.id), ["m3", "m4", "m5"])
        var other = BugReportState()
        other.received(message("m1", ticket: "bug-1", at: 1))
        XCTAssertTrue(other.replies.isEmpty, "no ticket yet, nothing to match")
    }

    func testTriagedKindOtherThanABugLeadsTheDetail() {
        var bug = BugReportState()
        bug.begin()
        bug.apply(BugReportEvent(state: "filed", ticketId: "bug-2", title: "Remember the Psalms at night"))
        bug.apply(BugReportEvent(state: "triaged", ticketId: "bug-2", priority: "P3", component: "other", disposition: "request", kind: "teaching"))
        XCTAssertEqual(bug.detail, "teaching · P3 · other · request")
        XCTAssertEqual(BugReportEvent(message: ["state": "triaged", "kind": "project_proposal"])?.kind, "project_proposal")
    }
}
