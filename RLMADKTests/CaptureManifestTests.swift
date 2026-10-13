import XCTest
@testable import RLMADK

final class CaptureManifestTests: XCTestCase {
    private func segment(_ i: Int, closed: Bool = true) -> SegmentRecord {
        SegmentRecord(index: i, filename: String(format: "%08d.wav", i), bytes: 1000 + i, sha256: String(repeating: "\(i)", count: 64), startedAt: Date(), endedAt: closed ? Date() : nil)
    }

    func testPendingUploadsAndNoDuplicateSubmission() {
        var m = CaptureManifest(captureId: "prayer-1", kind: "prayer", createdAt: Date())
        m.segments = [segment(0), segment(1), segment(2, closed: false)]
        XCTAssertEqual(m.pendingUploads.map(\.index), [0, 1]) // open segment is never uploaded
        XCTAssertNil(m.finalizeSegments()) // not closed
        m.recordUpload(index: 0, intakeSha256: m.segments[0].sha256, durable: true, error: nil)
        XCTAssertEqual(m.pendingUploads.map(\.index), [1])
        XCTAssertEqual(m.status, .segmentsUploaded(uploaded: 1, total: 3))
        // an intake receipt with a different digest is NOT an upload
        m.recordUpload(index: 1, intakeSha256: "deadbeef", durable: true, error: nil)
        XCTAssertEqual(m.pendingUploads.map(\.index), [1])
        m.recordUpload(index: 1, intakeSha256: m.segments[1].sha256, durable: true, error: nil)
        m.segments[2].endedAt = Date()
        m.recordUpload(index: 2, intakeSha256: m.segments[2].sha256, durable: true, error: nil)
        m.closedAt = Date()
        XCTAssertNil(m.finalizeSegments(), "closed but the writer has not settled")
        m.expectedSegmentCount = 3
        let manifest = m.finalizeSegments()
        XCTAssertEqual(manifest?.count, 3)
        XCTAssertEqual(manifest?[1]["segment_index"] as? Int, 1)
        XCTAssertEqual(manifest?[1]["audio_sha256"] as? String, m.segments[1].sha256)
        XCTAssertEqual(manifest?[1]["size_bytes"] as? Int, 1001)
    }

    func testUnreachableIntakeKeepsLocalRecordAndStatus() {
        var m = CaptureManifest(captureId: "prayer-2", kind: "prayer", createdAt: Date())
        m.segments = [segment(0)]
        m.recordUpload(index: 0, intakeSha256: nil, durable: nil, error: "intake_unreachable: connection refused")
        XCTAssertEqual(m.pendingUploads.count, 1)
        XCTAssertEqual(m.status, .intakeUnreachable(reason: "intake_unreachable: connection refused"))
        XCTAssertEqual(m.segments[0].lastError, "intake_unreachable: connection refused")
    }

    func testFinalizedStatusIsNotOverwrittenByLateUploadBookkeeping() {
        var m = CaptureManifest(captureId: "prayer-3", kind: "prayer", createdAt: Date())
        m.segments = [segment(0)]
        m.status = .finalized(recordingId: "rec1")
        m.recordUpload(index: 0, intakeSha256: m.segments[0].sha256, durable: true, error: nil)
        XCTAssertEqual(m.status, .finalized(recordingId: "rec1"))
    }

    func testGapRecordsAndCodableRoundTrip() throws {
        var m = CaptureManifest(captureId: "ambient-1", kind: "ambient", createdAt: Date())
        m.gaps.append(GapRecord(startedAt: Date(), endedAt: Date(), reason: "interruption_began", kind: "capture"))
        m.gaps.append(GapRecord(startedAt: Date(), endedAt: Date(), reason: "recognition_error_203", kind: "recognition"))
        m.segments = [segment(0)]
        let data = try JSONEncoder().encode([m])
        let back = try JSONDecoder().decode([CaptureManifest].self, from: data)
        XCTAssertEqual(back, [m])
        XCTAssertEqual(back[0].gaps.map(\.kind), ["capture", "recognition"])
    }

    func testCaptureIdFormatMatchesIntakeRule() {
        let id = CaptureManifest.newCaptureId(kind: "prayer")
        XCTAssertNotNil(id.range(of: "^[A-Za-z0-9_-]{1,128}$", options: .regularExpression))
        XCTAssertTrue(id.hasPrefix("prayer-"))
    }
}
