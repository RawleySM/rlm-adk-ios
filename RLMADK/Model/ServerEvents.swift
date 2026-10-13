import Foundation

/// JSON events on the text/control WebSocket. No audio travels on this socket.
struct ServerEvent: Decodable {
    var type: String
    var turn_id: String?
    var session_id: String?
    var seq: Int?
    var text: String?
    var message: String?
    var partial: String?
    var citations: [Citation]?
    var first_delta_ms: Int?
    var done_ms: Int?
    var model: String?
    var evidence_count: Int?
    var source_commit: String?
}

struct Citation: Codable, Equatable, Identifiable {
    var id: String { cite_id }
    var n: Int?
    var cite_id: String
    var source_family: String
    var title: String?
    var attribution: String?
    var verse_refs: [String]?
    var revision_hash: String?
}

struct CitationDetail: Decodable {
    var cite_id: String
    var available: Bool
    var reason: String?
    var verified: Bool?
    var current: Bool?
    var source_family: String?
    var source_doc_id: String?
    var revision_hash: String?
    var title: String?
    var attribution: String?
    var source_locator: String?
    var recorded_at: String?
    var recorded_at_basis: String?
    var observed_at: String?
    var text: String?
    var verse_locators: [VerseLocator]?

    struct VerseLocator: Decodable {
        var osis: String
        var verse: Int
        var start: Int
        var end: Int
    }
}

struct Devotional: Decodable {
    var available: Bool
    var reason: String?
    var passage: String
    var title: String?
    var revision_hash: String?
    var source_doc_id: String?
    var teaching_found: Bool?
    var segments: [Segment]?

    struct Segment: Decodable, Identifiable {
        var id: String { cite_id + (osis ?? "") }
        var kind: String
        var osis: String?
        var verse: Int?
        var text: String
        var cite_id: String
        var title: String?
        var attribution: String?
    }
}

struct SearchHit: Decodable, Identifiable {
    var id: String { cite_id }
    var cite_id: String
    var source_family: String
    var title: String?
    var snippet: String?
    var text: String
    var attribution: String?
    var recorded_at: String?
    var verse_refs: [String]?
}

struct SearchResponse: Decodable {
    var query: String
    var hits: [SearchHit]
}

struct WorkOperation: Decodable, Identifiable {
    var id: String { operation_id }
    var operation_id: String
    var kind: String
    var request_text: String
    var status: String
    var last_error: String?
    var note: String?
    var created_at: Double
    var attempts: [WorkAttempt]?
    var artifacts: [WorkArtifact]?

    struct WorkAttempt: Decodable, Identifiable {
        var id: Int { attempt_id }
        var attempt_id: Int
        var outcome: String
        var stdout: String?
        var stderr: String?
        var message_id: String?
        var run_id: String?
    }

    struct WorkArtifact: Decodable, Identifiable {
        var id: String { artifact_id }
        var artifact_id: String
        var kind: String
        var path: String
        var available: Bool
        var bytes: Int?
    }

    var statusLabel: String {
        switch status {
        case "queued_locally": return "Queued locally"
        case "backend_unavailable": return "Backend unavailable (held)"
        case "posting": return "Posting"
        case "delivery_unknown": return "Delivery unknown"
        case "directive_posted": return "Directive posted"
        case "artifact_available": return "Artifact available"
        case "failed": return "Failed"
        default: return status
        }
    }
}

struct WorkList: Decodable {
    var operations: [WorkOperation]
}

struct BackendStatus: Decodable {
    var service: String
    var version: String
    var source_commit: String?
    var state_root: String?
    var inference: Inference?
    var prayer_intake: Intake?
    var allocator: Allocator?
    var counts: [String: CountValue]?

    struct Inference: Decodable {
        var base_url: String
        var model: String
        var ready: Bool
        var advertised: Bool
        var model_phase: String?
        var errors: [String]?
    }

    struct Intake: Decodable {
        var url: String
        var reachable: Bool
        var source_commit: String?
        var error: String?
    }

    struct Allocator: Decodable {
        var available: Bool
        var active: String?
        var main_pid: Int?
        var launch_instance: String?
        var service_runs: [String]?
    }

    enum CountValue: Decodable {
        case family(current: Int, revisions: Int)
        case number(Int)

        init(from decoder: Decoder) throws {
            let c = try decoder.singleValueContainer()
            if let n = try? c.decode(Int.self) { self = .number(n); return }
            let d = try c.decode([String: Int].self)
            self = .family(current: d["current"] ?? 0, revisions: d["revisions"] ?? 0)
        }

        var text: String {
            switch self {
            case .family(let c, let r): return "\(c) current / \(r) revisions"
            case .number(let n): return "\(n)"
            }
        }
    }
}

struct CaptureStatusResponse: Decodable {
    var client_capture_id: String
    var status: String
    var recording_id: String?
    var prayer_id: String?
    var last_error: String?
    var intake_reachable: Bool?
    var intake_error: String?
}

struct SegmentUploadResponse: Decodable {
    var client_capture_id: String
    var segment_index: Int
    var status: String
    var verified: Bool?
    var reason: String?
    var audio_sha256: String?
    var size_bytes: Int?
    var receipt: Receipt?

    struct Receipt: Decodable {
        var audio_sha256: String?
        var size_bytes: Int?
        var durable: Bool?
        var reused: Bool?
    }
}

struct FinalizeResponse: Decodable {
    var client_capture_id: String
    var status: String
    var recording_id: String?
    var reason: String?
    var missing_or_unverified: [Int]?
}
