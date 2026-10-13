import Foundation

/// Explicit engagement state. `quiet` is a persisted, user-controlled flag that only `wake` clears.
/// `activity` is what the portal is doing right now; it never implies quiet.
struct PortalMode: Codable, Equatable {
    enum Activity: Codable, Equatable {
        case idle
        case listening(turnId: String)
        case responding(turnId: String)
        case prayer(captureId: String)
    }

    var quiet: Bool = false
    var activity: Activity = .idle
    /// Devotional playback position (segment index) kept independent of any connection.
    var devotionalPosition: Int = 0
    var devotionalPassage: String? = nil
    /// Exact source revision the position refers to; a different revision resets the position.
    var devotionalRevision: String? = nil

    var isPraying: Bool {
        if case .prayer = activity { return true }
        return false
    }

    var currentTurnId: String? {
        switch activity {
        case .listening(let id), .responding(let id): return id
        default: return nil
        }
    }

    var prayerCaptureId: String? {
        if case .prayer(let id) = activity { return id }
        return nil
    }

    var label: String {
        if isPraying { return quiet ? "Prayer (quiet)" : "Prayer" }
        if quiet { return "Quiet" }
        switch activity {
        case .idle: return "Available"
        case .listening: return "Listening"
        case .responding: return "Responding"
        case .prayer: return "Prayer"
        }
    }
}

/// Deterministic output gate: speech may be enqueued only when not quiet and not praying.
/// It is evaluated before enqueue, independently of anything the model says.
enum SpeechGate {
    static func mayEnqueue(_ mode: PortalMode) -> Bool {
        !mode.quiet && !mode.isPraying
    }
}

enum PortalEvent: Equatable {
    case userSpeechStarted
    case finalTranscript(turnId: String)
    case assistantDelta(turnId: String)
    case assistantDone(turnId: String)
    case assistantCancelled(turnId: String)
    case stopRequested
    case enterPrayer(captureId: String)
    case endPrayer
    case enterQuiet
    case wake
    case silence(seconds: Double)
    case connectionLost
    case connectionRestored
    case relaunch
}

enum PortalEffect: Equatable {
    case cancelOutput(turnId: String?)
    case sendCancel(turnId: String)
    case startTurn(turnId: String)
    case savePrayer(captureId: String)
    case waitForUser
}

/// Pure reducer. Tests cover every acceptance rule in the plan.
enum PortalReducer {
    static func apply(_ event: PortalEvent, to state: PortalMode) -> (PortalMode, [PortalEffect]) {
        var next = state
        var effects: [PortalEffect] = []
        switch event {
        case .userSpeechStarted:
            if case .responding(let id) = state.activity {
                // Barge-in: current and queued output are cancelled; old-turn deltas are rejected downstream.
                effects.append(.cancelOutput(turnId: id))
                effects.append(.sendCancel(turnId: id))
                next.activity = .idle
            } else if !state.isPraying {
                // Real speech also interrupts output that is still playing after the turn finished
                // (trailing TTS, devotional playback). Prayer speech is never an interruption.
                effects.append(.cancelOutput(turnId: nil))
            }
        case .finalTranscript(let turnId):
            if state.isPraying { break } // prayer is addressed to God; transcripts never start a turn
            if let pending = state.currentTurnId, pending != turnId {
                // The new utterance supersedes a turn that is still pending or speaking.
                effects.append(.cancelOutput(turnId: pending))
                effects.append(.sendCancel(turnId: pending))
            }
            next.activity = .responding(turnId: turnId)
            effects.append(.startTurn(turnId: turnId))
        case .assistantDelta:
            break
        case .assistantDone(let turnId), .assistantCancelled(let turnId):
            if case .responding(let id) = state.activity, id == turnId {
                next.activity = .idle // ordinary completion returns to available, never to quiet
            }
        case .stopRequested:
            if let id = state.currentTurnId {
                effects.append(.cancelOutput(turnId: id))
                effects.append(.sendCancel(turnId: id))
                next.activity = .idle
            } else {
                effects.append(.cancelOutput(turnId: nil))
            }
        case .enterPrayer(let captureId):
            // Entering prayer always silences current and queued speech, whatever was playing.
            effects.append(.cancelOutput(turnId: state.currentTurnId))
            if let id = state.currentTurnId {
                effects.append(.sendCancel(turnId: id))
            }
            next.activity = .prayer(captureId: captureId)
        case .endPrayer:
            if case .prayer(let captureId) = state.activity {
                next.activity = .idle // quiet is untouched: pre-existing quiet remains
                effects.append(.savePrayer(captureId: captureId))
                effects.append(.waitForUser)
            }
        case .enterQuiet:
            next.quiet = true
            if let id = state.currentTurnId {
                effects.append(.cancelOutput(turnId: id))
                effects.append(.sendCancel(turnId: id))
                next.activity = .idle
            } else {
                effects.append(.cancelOutput(turnId: nil))
            }
        case .wake:
            next.quiet = false
        case .silence:
            break // silence never ends prayer, never changes mode, never produces output
        case .connectionLost, .connectionRestored, .relaunch:
            break // mode is never derived from connection or process lifetime
        }
        return (next, effects)
    }
}
