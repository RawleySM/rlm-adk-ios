import CryptoKit
import Foundation
import XCTest
@testable import RLMADK

/// Runs the actual PortalModel upload/finalize paths through URLSession, holding responses at
/// precise suspension points to reproduce overlapping writer, settle and retry callbacks.
@MainActor
final class PrayerUploadTests: XCTestCase {
    private var roots: [URL] = []

    override func tearDown() {
        PrayerUploadProtocol.handler = nil
        for root in roots { try? FileManager.default.removeItem(at: root) }
        roots = []
        super.tearDown()
    }

    private func fixture(closed: Bool = true, secondCapture: Bool = false) throws -> (PortalModel, LocalStore, String) {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        roots.append(root)
        let store = LocalStore(root: root)
        let id = "prayer-test-\(UUID().uuidString)"
        var capture = CaptureManifest(captureId: id, kind: "prayer", createdAt: Date())
        capture.segments = [try segment(store, id, 0)]
        if closed { capture.settle(produced: 1) }
        var initial = [capture]
        if secondCapture {
            var other = CaptureManifest(captureId: id + "-other", kind: "prayer", createdAt: Date())
            other.segments = [try segment(store, other.captureId, 0)]
            other.settle(produced: 1)
            initial.append(other)
        }
        store.saveCaptures(initial)
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [PrayerUploadProtocol.self]
        let client = PortalClient(baseURL: URL(string: "http://upload.test")!, configuration: config)
        let model = PortalModel(store: store, client: client)
        model.connection = .connected(sessionId: "fixture")
        return (model, store, id)
    }

    private func segment(_ store: LocalStore, _ id: String, _ index: Int) throws -> SegmentRecord {
        let bytes = Data("retained-test-audio-\(index)".utf8)
        let dir = store.captureDirectory(id)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let name = String(format: "%08d.wav", index)
        try bytes.write(to: dir.appendingPathComponent(name))
        return SegmentRecord(index: index, filename: name, bytes: bytes.count,
                             sha256: SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined(),
                             startedAt: Date(), endedAt: Date())
    }

    func testOverlappingUploadAndFinalizeSendOnlyOneRequestEach() async throws {
        let (model, _, id) = try fixture()
        let uploadEntered = expectation(description: "upload suspended")
        let finalizeEntered = expectation(description: "finalize suspended")
        var pendingUpload: PrayerUploadProtocol?
        var pendingFinalize: PrayerUploadProtocol?
        var paths: [String] = []
        PrayerUploadProtocol.handler = { p in
            paths.append(p.request.url!.path)
            if p.request.url!.path.hasSuffix("/finalize") {
                pendingFinalize = p; finalizeEntered.fulfill()
            } else {
                pendingUpload = p; uploadEntered.fulfill()
            }
        }
        let first = Task { await model.uploadPending(captureId: id) }
        await fulfillment(of: [uploadEntered], timeout: 3)
        await model.uploadPending(captureId: id) // capture-settled/retry during segment HTTP
        pendingUpload?.succeed(id)
        await fulfillment(of: [finalizeEntered], timeout: 3)
        await model.uploadPending(captureId: id) // reconnect/retry during finalize HTTP
        pendingFinalize?.succeed(id)
        await first.value
        XCTAssertEqual(paths, ["/prayer/captures/\(id)/segments/0", "/prayer/captures/\(id)/finalize"])
        XCTAssertEqual(model.captures.first { $0.captureId == id }?.status, .finalized(recordingId: "recording-fixture"))
    }

    func testLastSegmentAndSettlementDuringUploadAreNotDropped() async throws {
        let (model, store, id) = try fixture(closed: false)
        let entered = expectation(description: "first upload suspended")
        var held: PrayerUploadProtocol?
        var paths: [String] = []
        PrayerUploadProtocol.handler = { p in
            paths.append(p.request.url!.path)
            if paths.count == 1 { held = p; entered.fulfill() } else { p.succeed(id) }
        }
        let first = Task { await model.uploadPending(captureId: id) }
        await fulfillment(of: [entered], timeout: 3)
        model.segmentClosed(captureId: id, try segment(store, id, 1))
        model.captureSettledForTest(id, produced: 2)
        // Explicitly enter the same callback path before releasing HTTP, avoiding scheduler timing.
        await model.uploadPending(captureId: id)
        held?.succeed(id)
        await first.value
        XCTAssertEqual(paths, ["/prayer/captures/\(id)/segments/0", "/prayer/captures/\(id)/segments/1", "/prayer/captures/\(id)/finalize"])
        let capture = model.captures.first { $0.captureId == id }!
        XCTAssertTrue(capture.allUploaded)
        XCTAssertEqual(capture.expectedSegmentCount, 2)
        XCTAssertEqual(capture.status, .finalized(recordingId: "recording-fixture"))
    }

    func testFailedUploadReleasesOwnerForLaterRetry() async throws {
        let (model, _, id) = try fixture()
        var requests = 0
        PrayerUploadProtocol.handler = { p in
            requests += 1
            if requests == 1 { p.reply(status: 503, body: [:]) } else { p.succeed(id) }
        }
        await model.uploadPending(captureId: id)
        XCTAssertEqual(requests, 1, "no automatic busy retry after a network failure")
        XCTAssertEqual(model.captures.first { $0.captureId == id }?.pendingUploads.count, 1)
        await model.uploadPending(captureId: id)
        XCTAssertEqual(requests, 3, "one failed upload, one successful retry, one finalize")
        XCTAssertEqual(model.captures.first { $0.captureId == id }?.status, .finalized(recordingId: "recording-fixture"))
    }

    func testDifferentCapturesCanUploadWhileOneIsSuspended() async throws {
        let (model, _, id) = try fixture(secondCapture: true)
        let second = id + "-other"
        let entered = expectation(description: "first capture held")
        var held: PrayerUploadProtocol?
        PrayerUploadProtocol.handler = { p in
            if p.request.url!.path == "/prayer/captures/\(id)/segments/0" {
                held = p; entered.fulfill()
            } else { p.succeed(p.request.url!.path.contains(second) ? second : id) }
        }
        let first = Task { await model.uploadPending(captureId: id) }
        await fulfillment(of: [entered], timeout: 3)
        await model.uploadPending(captureId: second)
        XCTAssertEqual(model.captures.first { $0.captureId == second }?.status, .finalized(recordingId: "recording-fixture"))
        held?.succeed(id)
        await first.value
    }
}

private final class PrayerUploadProtocol: URLProtocol {
    @MainActor static var handler: ((PrayerUploadProtocol) -> Void)?
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host == "upload.test" }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() { Task { @MainActor in Self.handler?(self) } }
    override func stopLoading() {}

    @MainActor func succeed(_ id: String) {
        if request.url!.path.hasSuffix("/finalize") {
            reply(status: 200, body: ["client_capture_id": id, "status": "finalized", "recording_id": "recording-fixture"])
        } else {
            reply(status: 200, body: ["client_capture_id": id, "segment_index": Int(request.url!.lastPathComponent)!,
                "status": "uploaded", "verified": true,
                "receipt": ["audio_sha256": request.value(forHTTPHeaderField: "X-Audio-SHA256")!, "durable": true]])
        }
    }
    @MainActor func reply(status: Int, body: [String: Any]) {
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: try! JSONSerialization.data(withJSONObject: body))
        client?.urlProtocolDidFinishLoading(self)
    }
}
