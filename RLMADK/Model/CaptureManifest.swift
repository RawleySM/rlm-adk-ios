import Foundation

/// Prayer intake status set from the plan. Each value is a distinct observation, never inferred.
enum PrayerStatus: Codable, Equatable {
    case savedLocally
    case segmentsUploaded(uploaded: Int, total: Int)
    case finalized(recordingId: String)
    case published(prayerId: String)
    case excluded
    case failed(reason: String)
    case intakeUnreachable(reason: String)

    var label: String {
        switch self {
        case .savedLocally: return "Saved locally"
        case .segmentsUploaded(let k, let n): return "Segments uploaded \(k)/\(n)"
        case .finalized(let id): return "Finalized (\(id.prefix(8)))"
        case .published(let id): return "Published (\(id.prefix(8)))"
        case .excluded: return "Excluded by intake"
        case .failed(let r): return "Failed: \(r)"
        case .intakeUnreachable: return "Saved locally; intake unreachable"
        }
    }
}

struct SegmentRecord: Codable, Equatable, Identifiable {
    var id: Int { index }
    var index: Int
    var filename: String
    var bytes: Int
    var sha256: String
    var startedAt: Date
    var endedAt: Date?
    var uploadedAt: Date? = nil
    var intakeSha256: String? = nil
    var intakeDurable: Bool? = nil
    var lastError: String? = nil

    /// A segment counts as uploaded only when the intake echoed the same digest and length.
    var verifiedUploaded: Bool { uploadedAt != nil && intakeSha256 == sha256 && intakeDurable == true }
}

struct GapRecord: Codable, Equatable, Identifiable {
    var id: String { "\(startedAt.timeIntervalSince1970)-\(reason)" }
    var startedAt: Date
    var endedAt: Date?
    var reason: String // interruption_began, route_change, engine_error, recognition_restart ...
    var kind: String // "capture" (audio not recorded) or "recognition" (audio recorded, no transcript)
}

/// Locally persisted capture: survives connection loss and relaunch. Identity is the stable
/// `client_capture_id`; uploads reuse it and never create a second capture for the same prayer.
struct CaptureManifest: Codable, Equatable, Identifiable {
    var id: String { captureId }
    var captureId: String
    var kind: String // "prayer" or "ambient"
    var createdAt: Date
    var closedAt: Date? = nil
    var segments: [SegmentRecord] = []
    var gaps: [GapRecord] = []
    var status: PrayerStatus = .savedLocally
    var recordingId: String? = nil
    var prayerId: String? = nil
    var finalizeRequestedAt: Date? = nil

    var pendingUploads: [SegmentRecord] { segments.filter { $0.endedAt != nil && !$0.verifiedUploaded } }
    var allUploaded: Bool { !segments.isEmpty && segments.allSatisfy { $0.endedAt != nil && $0.verifiedUploaded } }
    var isClosed: Bool { closedAt != nil }

    /// Ordered manifest for `POST /capture-finalize`; nil until every segment is verified.
    func finalizeSegments() -> [[String: Any]]? {
        guard isClosed, allUploaded else { return nil }
        return segments.sorted { $0.index < $1.index }.enumerated().map { offset, seg in
            precondition(offset == seg.index, "segment indexes must be contiguous")
            return ["segment_index": seg.index, "audio_sha256": seg.sha256, "size_bytes": seg.bytes]
        }
    }

    mutating func recordUpload(index: Int, intakeSha256: String?, durable: Bool?, error: String?) {
        guard let i = segments.firstIndex(where: { $0.index == index }) else { return }
        segments[i].lastError = error
        if error == nil {
            segments[i].uploadedAt = Date()
            segments[i].intakeSha256 = intakeSha256
            segments[i].intakeDurable = durable
        }
        let verified = segments.filter { $0.verifiedUploaded }.count
        if case .finalized = status { return }
        if case .published = status { return }
        if let error, error.hasPrefix("intake_unreachable") {
            status = .intakeUnreachable(reason: error)
        } else if verified > 0 || error == nil {
            status = .segmentsUploaded(uploaded: verified, total: segments.count)
        }
    }

    static func newCaptureId(kind: String, now: Date = Date()) -> String {
        let stamp = Int(now.timeIntervalSince1970)
        let random = UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(8).lowercased()
        return "\(kind)-\(stamp)-\(random)"
    }
}
