import Foundation

/// What the backend may see of the phone: app-owned state only, bounded, timestamped on the phone.
/// This is not screen mirroring; the keyboard, permission alerts and other apps are outside it.
struct DeviceSnapshot: Codable, Equatable {
    var install_id: String
    var client_time: Double
    var app_version: String
    var build: String
    var scene_phase: String
    var selected_tab: String
    var connection: String
    var session_id: String?
    var mode: String
    var quiet: Bool
    var praying: Bool
    var audio_owner_state: String
    var engine_running: Bool
    var last_buffer_age_s: Double?
    var last_write_error: String?
    var recognizer: String
    var recognition_continuous: Bool
    var recognition_listening: Bool
    var keyboard_visible: Bool
    var keyboard_frame: [Double]?   // x, y, width, height in points while visible
    var focused_field: String?
    var typed_input: String
    var partial_transcript: String
    var last_transcript: String
    var assistant_text_tail: String
    var capture_warning: String?
    var last_journal_error: String?
    var captures: [CaptureSummary]
    var recent_log: [String]
    var last_error: String?

    struct CaptureSummary: Codable, Equatable {
        var capture_id: String
        var kind: String
        var status: String
        var segments: Int
        var bytes: Int
        var pending_uploads: Int
        var gaps: Int
        var closed: Bool
        var expected_segments: Int?
        var created_at: Double
        var last_segment_end: Double?
    }
}

/// Remote commands the backend may send over the existing text/control socket.
enum RemoteCommandKind: String {
    case snapshot, image, dismiss_keyboard, select_tab, copy_capture_files, sync_manifest, sync_prayer_asr
}

struct RemoteCommand: Decodable {
    var command_id: String
    var kind: String
    var args: [String: AnyCodableValue]?
}

/// Minimal JSON value for command arguments (strings, numbers, bools, arrays of those).
enum AnyCodableValue: Decodable, Equatable {
    case string(String), number(Double), bool(Bool), array([AnyCodableValue]), null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null; return }
        if let b = try? c.decode(Bool.self) { self = .bool(b); return }
        if let n = try? c.decode(Double.self) { self = .number(n); return }
        if let s = try? c.decode(String.self) { self = .string(s); return }
        if let a = try? c.decode([AnyCodableValue].self) { self = .array(a); return }
        throw DecodingError.dataCorruptedError(in: c, debugDescription: "unsupported argument")
    }

    var stringValue: String? { if case .string(let s) = self { return s } else { return nil } }
    var intArray: [Int]? {
        if case .array(let a) = self { return a.compactMap { if case .number(let n) = $0 { return Int(n) } else { return nil } } }
        return nil
    }
}

/// Phone-side result of a command. `applied` means the action actually ran on the phone; anything
/// the phone could not do is `failed` (with a reason) or `unsupported` (unknown kind).
struct CommandResult: Codable, Equatable {
    enum Status: String, Codable { case applied, failed, unsupported }
    var commandId: String
    var status: Status
    var detail: [String: String]
    var reportedAt: Double = Date().timeIntervalSince1970

    func payload() -> [String: Any] {
        ["type": "command_result", "command_id": commandId, "status": status.rawValue, "result": detail]
    }
}

struct PlanError: Error, Equatable {
    var reason: String
}

/// Pure planning for commands whose outcome depends only on local facts, so edge cases are testable
/// without the UI: unknown tab, unknown capture, requested files that are not on disk.
enum RemoteCommandPlanner {
    static let tabs = ["portal", "sources", "work", "diagnostics"]

    struct PlannedFile: Equatable {
        var index: Int
        var filename: String
        var manifestSha256: String?       // nil for a file the manifest does not list (orphan)
    }

    struct FileCopyPlan: Equatable {
        var captureId: String
        var files: [PlannedFile]          // closed files present on disk, ascending index
        var missingIndexes: [Int]         // named indexes with no closed file on disk
        var inManifest: Bool
    }

    static func selectTab(_ args: [String: AnyCodableValue]?) -> Result<String, PlanError> {
        guard let tab = args?["tab"]?.stringValue?.lowercased() else { return .failure(PlanError(reason: "missing tab")) }
        return tabs.contains(tab) ? .success(tab) : .failure(PlanError(reason: "unknown tab \(tab); known: \(tabs.joined(separator: ","))"))
    }

    /// `closedFilesOnDisk` lists index -> filename of segment files on disk for the capture, excluding
    /// the file a writer is still appending to. A capture known only by its directory (no manifest
    /// entry) is still copyable by exact identity; a capture with neither is unknown.
    static func copyPlan(_ args: [String: AnyCodableValue]?, captures: [CaptureManifest], closedFilesOnDisk: (String) -> [Int: String]) -> Result<FileCopyPlan, PlanError> {
        guard let captureId = args?["capture_id"]?.stringValue else { return .failure(PlanError(reason: "missing capture_id")) }
        let capture = captures.first(where: { $0.captureId == captureId })
        let onDisk = closedFilesOnDisk(captureId)
        guard capture != nil || !onDisk.isEmpty else { return .failure(PlanError(reason: "unknown capture \(captureId)")) }
        let manifestClosed = (capture?.segments ?? []).filter { $0.endedAt != nil }
        let manifestSha = Dictionary(manifestClosed.map { ($0.index, $0.sha256) }, uniquingKeysWith: { a, _ in a })
        let requested = args?["indexes"]?.intArray
        let wanted = requested.map { Set($0) } ?? Set(manifestClosed.map(\.index)).union(onDisk.keys)
        var files: [PlannedFile] = []
        var missing: [Int] = []
        for index in wanted.sorted() {
            if let name = onDisk[index] {
                files.append(PlannedFile(index: index, filename: name, manifestSha256: manifestSha[index]))
            } else {
                missing.append(index)
            }
        }
        return .success(FileCopyPlan(captureId: captureId, files: files, missingIndexes: missing, inManifest: capture != nil))
    }
}

/// Durable, append-only on-device journal of provisional prayer ASR lines, keyed by capture id and
/// sequence, so a resend after reconnect cannot duplicate on the backend.
struct PrayerASRJournal: Equatable {
    struct Line: Codable, Equatable {
        var sequence: Int
        var client_time: Double
        var text: String
    }

    var captureId: String
    private(set) var lines: [Line] = []
    private(set) var syncedThrough: Int = 0

    init(captureId: String, lines: [Line] = [], syncedThrough: Int = 0) {
        self.captureId = captureId
        self.lines = lines.sorted { $0.sequence < $1.sequence }
        self.syncedThrough = syncedThrough
    }

    @discardableResult
    mutating func append(_ text: String, at time: Date = Date()) -> Line {
        let line = Line(sequence: (lines.map(\.sequence).max() ?? 0) + 1, client_time: time.timeIntervalSince1970, text: text)
        lines.append(line)
        return line
    }

    var unsynced: [Line] { lines.filter { $0.sequence > syncedThrough } }

    mutating func markSynced(through sequence: Int) { syncedThrough = max(syncedThrough, sequence) }
}
