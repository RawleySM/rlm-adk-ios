import XCTest
@testable import RLMADK

/// The Bug chip: `bug_report` / `bug_text` messages, `client_bug` on the wire, the chip's state, and
/// the controller (speech gate closed while reporting, phrases and addresses inert, typed lines join
/// the report, the stop carries the phone's own lines).
@MainActor
final class BugReportTests: XCTestCase {
    private var roots: [URL] = []

    override func tearDown() {
        for root in roots { try? FileManager.default.removeItem(at: root) }
        roots = []
        super.tearDown()
    }

    private func makeModel() -> (PortalModel, FakeTalkClient) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("bug-\(UUID().uuidString)")
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

    private func bugMessages(_ talk: FakeTalkClient) -> [(String, [String: String])] {
        talk.sent.compactMap { if case .clientBug(let action, let context, let lines) = $0 { return (action, TalkClientMessage.clientBug(action: action, context: context, deviceLines: lines).fields) } else { return nil } }
    }

    func testMessagesParse() {
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"bug_report","state":"filed","report_id":"r1","ticket_id":"bug-20261015-03","title":"Shading lags","segments":2}"#),
                       [.bugReport(BugReportEvent(state: "filed", reportId: "r1", ticketId: "bug-20261015-03", title: "Shading lags", segments: 2))])
        XCTAssertEqual(TalkMessageParser.events(fromText: #"{"type":"bug_text","text":"The shading is behind.","final":true,"source":"voice"}"#),
                       [.bugText(text: "The shading is behind.", final: true)])
        let wrapped = #"{"label":"rtvi-ai","type":"server-message","data":{"type":"bug_report","state":"started","report_id":"r1"}}"#
        XCTAssertEqual(TalkMessageParser.events(fromText: wrapped), [.bugReport(BugReportEvent(state: "started", reportId: "r1"))])
    }

    func testClientBugOnTheWire() {
        let stop = TalkClientMessage.clientBug(action: "stop", context: ["app_build": "17", "client": "ios"], deviceLines: ["the shading", "is behind"])
        XCTAssertEqual(stop.json["type"] as? String, "client_bug")
        XCTAssertEqual(stop.json["action"] as? String, "stop")
        XCTAssertEqual(stop.json["app_build"] as? String, "17")
        XCTAssertEqual(stop.json["device_segments"] as? String, "the shading\nis behind")
        XCTAssertNil(TalkClientMessage.clientBug(action: "start", context: [:], deviceLines: []).json["device_segments"])
    }

    func testChipStates() {
        var bug = BugReportState()
        XCTAssertEqual(bug.chipLabel, "Bug")
        XCTAssertFalse(bug.silencesOutput)
        bug.begin()
        XCTAssertEqual(bug.chipLabel, "Bug Reporting...")
        XCTAssertTrue(bug.silencesOutput)
        bug.apply(BugReportEvent(state: "started", reportId: "r1"))
        bug.heard("The shading", final: false)
        XCTAssertEqual(bug.interim, "The shading")
        bug.heard("The shading is behind the voice.", final: true)
        XCTAssertEqual(bug.lines, ["The shading is behind the voice."])
        XCTAssertEqual(bug.interim, "")
        bug.apply(BugReportEvent(state: "filing", reportId: "r1"))
        XCTAssertEqual(bug.chipLabel, "Filing…")
        XCTAssertTrue(bug.silencesOutput)
        bug.apply(BugReportEvent(state: "filed", reportId: "r1", ticketId: "bug-20261015-03", title: "Shading lags"))
        XCTAssertFalse(bug.silencesOutput)
        XCTAssertEqual(bug.chipLabel, "Bug")
        XCTAssertEqual(bug.outcome, "Filed bug-20261015-03: Shading lags")
        bug.apply(BugReportEvent(state: "triaged", reportId: "r1", ticketId: "bug-20261015-03", title: "Verse shading lags the voice", priority: "P2", component: "scripture_pane", disposition: "ready"))
        XCTAssertEqual(bug.outcome, "Filed bug-20261015-03: Verse shading lags the voice — P2 · scripture pane · ready")
        bug.heard("late words", final: true)
        XCTAssertEqual(bug.lines.count, 1, "nothing is added after the report ended")
    }

    func testLateMessagesOfAnotherReportAreIgnored() {
        var bug = BugReportState()
        bug.begin()
        bug.apply(BugReportEvent(state: "started", reportId: "r2"))
        bug.apply(BugReportEvent(state: "filed", reportId: "r1", ticketId: "bug-1"))
        XCTAssertEqual(bug.phase, .reporting)
    }

    func testNoneAndConnectionLossFailAnOpenReport() {
        var bug = BugReportState()
        bug.begin()
        bug.apply(BugReportEvent(state: "filing"))
        bug.apply(BugReportEvent(state: "none"))
        XCTAssertEqual(bug.phase, .failed)
        var other = BugReportState()
        other.begin()
        other.connectionLost()
        XCTAssertEqual(other.phase, .failed)
        XCTAssertNotNil(other.outcome)
    }

    func testReportingClosesTheGateAndKeepsWordsOutOfCommands() throws {
        let (model, talk) = makeModel()
        XCTAssertTrue(talk.fakeVoice.gateOpen)
        model.toggleBugReport()
        XCTAssertEqual(model.bug.phase, .reporting)
        XCTAssertFalse(talk.fakeVoice.gateOpen, "the phone's own gate closes while reporting")
        XCTAssertGreaterThanOrEqual(talk.fakeVoice.stops, 1)
        let start = try XCTUnwrap(bugMessages(talk).first)
        XCTAssertEqual(start.0, "start")
        XCTAssertEqual(start.1["client"], "ios")
        XCTAssertNotNil(start.1["app_build"])
        // An address or a quiet phrase on the phone's own recognizer does nothing while reporting.
        model.partialTranscriptReceived("Lord")
        model.finalTranscriptReceived("Lord, quiet please")
        XCTAssertFalse(model.mode.isPraying)
        XCTAssertFalse(model.mode.quiet)
        XCTAssertEqual(model.bug.deviceLines, ["Lord, quiet please"])
        // Typed text goes to the server, which adds it to the report.
        model.typedInput = "It happens at verse five."
        model.submitTyped()
        XCTAssertEqual(talk.texts.last, "It happens at verse five.")
        model.talkEventReceived(.bugText(text: "It happens at verse five.", final: true))
        XCTAssertEqual(model.bug.lines, ["It happens at verse five."])
        // The second tap files it, with the phone's lines.
        model.toggleBugReport()
        XCTAssertEqual(model.bug.phase, .filing)
        let stop = try XCTUnwrap(bugMessages(talk).last)
        XCTAssertEqual(stop.0, "stop")
        XCTAssertEqual(stop.1["device_segments"], "Lord, quiet please")
        XCTAssertFalse(talk.fakeVoice.gateOpen, "still silent while filing")
        model.talkEventReceived(.bugReport(BugReportEvent(state: "filed", ticketId: "bug-20261015-01", title: "Shading lags")))
        XCTAssertEqual(model.bug.phase, .filed)
        XCTAssertTrue(talk.fakeVoice.gateOpen, "the gate reopens once the report is filed")
        model.dismissBugOutcome()
        XCTAssertEqual(model.bug.phase, .idle)
    }

    func testCancelDropsTheReport() throws {
        let (model, talk) = makeModel()
        model.startBugReport()
        model.cancelBugReport()
        XCTAssertEqual(model.bug.phase, .idle)
        XCTAssertEqual(bugMessages(talk).map(\.0), ["start", "cancel"])
        XCTAssertTrue(talk.fakeVoice.gateOpen)
    }

    func testNoReportDuringPrayer() {
        let (model, talk) = makeModel()
        model.beginPrayer()
        model.toggleBugReport()
        XCTAssertEqual(model.bug.phase, .idle)
        XCTAssertTrue(bugMessages(talk).isEmpty)
    }
}
