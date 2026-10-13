import Foundation

/// Devotional playback progress driven by actual speech callbacks, not by enqueue order.
struct DevotionalProgress: Codable, Equatable {
    var passage: String
    var revision: String
    var total: Int
    /// Index of the segment currently being spoken, or the next one to speak.
    var resumeIndex: Int = 0
    var finished: Bool = false

    /// A segment actually began playing.
    mutating func started(_ index: Int) {
        resumeIndex = index
        finished = false
    }

    /// A segment finished playing; the next one becomes the resume point.
    mutating func finished(_ index: Int) {
        if index + 1 >= total {
            finished = true
            resumeIndex = index
        } else {
            resumeIndex = index + 1
        }
    }

    /// Interruption keeps the segment that was playing so it is repeated in full on resume.
    mutating func interrupted() {}

    /// A different passage or source revision invalidates the position.
    func matches(passage: String, revision: String) -> Bool {
        self.passage == passage && self.revision == revision
    }
}

/// Locally durable work request. It exists on the phone before the service knows about it, keeps
/// its operation id across relaunch, and is settled only when the service confirms the record.
struct LocalWorkItem: Codable, Equatable, Identifiable {
    var id: String { operationId }
    var operationId: String
    var kind: String
    var requestText: String
    var createdAt: Date
    /// pending: not yet confirmed on the service. recorded: service has it (dispatch is the service's job).
    var state: String = "pending"
    var lastError: String? = nil
}

enum WorkReconcileAction: Equatable {
    case create(operationId: String)
    case settle(operationId: String)
}

enum WorkReconciler {
    /// Decides, per pending item, whether the service must be asked to create the operation (same
    /// id) or whether it already exists and the local record can be settled. Never creates twice.
    static func plan(pending: [LocalWorkItem], serverHas: (String) -> Bool) -> [WorkReconcileAction] {
        pending.filter { $0.state == "pending" }.map { item in
            serverHas(item.operationId) ? .settle(operationId: item.operationId) : .create(operationId: item.operationId)
        }
    }
}
