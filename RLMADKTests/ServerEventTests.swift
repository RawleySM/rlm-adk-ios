import XCTest
@testable import RLMADK

final class ServerEventTests: XCTestCase {
    func testDecodesDeltaAndDone() throws {
        let delta = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"assistant_text_delta","turn_id":"t1","seq":3,"text":"He restores"}"#.utf8))
        XCTAssertEqual(delta.type, "assistant_text_delta")
        XCTAssertEqual(delta.seq, 3)
        let done = try JSONDecoder().decode(ServerEvent.self, from: Data(#"{"type":"assistant_done","turn_id":"t1","text":"x","citations":[{"n":1,"cite_id":"12:abcdefabcdef:0","source_family":"scripture","title":"Psalms 23","attribution":"Scripture, WEB","verse_refs":["Ps.23.1"]}],"first_delta_ms":812,"done_ms":4100,"model":"spark-small","evidence_count":1}"#.utf8))
        XCTAssertEqual(done.citations?.first?.verse_refs, ["Ps.23.1"])
        XCTAssertEqual(done.first_delta_ms, 812)
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
