import XCTest
@testable import RLMADK

final class PhraseQueueTests: XCTestCase {
    func testSplitsCompletePhrasesIncrementally() {
        var q = PhraseQueue()
        q.beginTurn("t1")
        XCTAssertEqual(q.accept(delta: "The LORD is my", turnId: "t1"), [])
        XCTAssertEqual(q.accept(delta: " shepherd. I shall", turnId: "t1"), ["The LORD is my shepherd."])
        XCTAssertEqual(q.accept(delta: " not want.", turnId: "t1"), []) // waits for trailing space or finish
        XCTAssertEqual(q.finish(turnId: "t1"), ["I shall not want."])
        XCTAssertNil(q.currentTurnId)
    }

    func testOldTurnDeltasAreDroppedAfterCancel() {
        var q = PhraseQueue()
        q.beginTurn("t1")
        _ = q.accept(delta: "Hello there. More", turnId: "t1")
        q.cancelAll()
        XCTAssertEqual(q.accept(delta: " text arrives late. ", turnId: "t1"), [])
        XCTAssertEqual(q.finish(turnId: "t1"), [])
        q.beginTurn("t2")
        XCTAssertEqual(q.accept(delta: "New turn. ", turnId: "t2"), ["New turn."])
        XCTAssertEqual(q.accept(delta: "Stale. ", turnId: "t1"), [])
    }

    func testWrongTurnIgnored() {
        var q = PhraseQueue()
        q.beginTurn("a")
        XCTAssertEqual(q.accept(delta: "x. ", turnId: "b"), [])
        XCTAssertEqual(q.buffer, "")
    }
}
