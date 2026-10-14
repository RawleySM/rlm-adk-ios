import XCTest
@testable import RLMADK

final class ServerEventTests: XCTestCase {
    func testDecodesSessionAndErrorEvents() throws {
        let session = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"session","session_id":"abc123","source_commit":"deadbeef"}"#.utf8))
        XCTAssertEqual(session.type, "session")
        XCTAssertEqual(session.session_id, "abc123")
        let error = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"error","message":"nope","turn_id":"ignored"}"#.utf8))
        XCTAssertEqual(error.message, "nope")
    }

    func testDecodesStatusCounts() throws {
        let status = try JSONDecoder().decode(BackendStatus.self, from: Data(#"{"service":"voice-portal","version":"0.1.0","counts":{"prayer":{"current":209,"revisions":209},"theographic_people":3069}}"#.utf8))
        XCTAssertEqual(status.counts?["prayer"]?.text, "209 current / 209 revisions")
        XCTAssertEqual(status.counts?["theographic_people"]?.text, "3069")
    }

    func testWorkStatusLabels() throws {
        let op = try JSONDecoder().decode(WorkOperation.self, from: Data(#"{"operation_id":"abc","kind":"research","request_text":"x","status":"delivery_unknown","created_at":1.0}"#.utf8))
        XCTAssertEqual(op.statusLabel, "Delivery unknown")
    }
}
