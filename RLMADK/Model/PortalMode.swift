import Foundation

/// Explicit engagement state. `quiet` is a persisted, user-controlled flag that only `wake` clears.
/// `activity` is what the portal is doing right now; it never implies quiet.
struct PortalMode: Codable, Equatable {
    /// `listening` is kept only so mode files written by earlier builds still decode; nothing
    /// enters it now. `responding` means the talk tier's bot is speaking (turn id minted here).
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
        case .responding: return "Speaking"
        case .prayer: return "Prayer"
        }
    }
}

/// Deterministic output gate: bot speech may play only when not quiet and not praying. It is
/// evaluated on the phone before any bot audio is played, independently of anything the talk
/// server says or sends.
enum SpeechGate {
    static func mayEnqueue(_ mode: PortalMode) -> Bool {
        !mode.quiet && !mode.isPraying
    }
}

/// Floor mode the talk server is told about (`client_mode`): the phone's persisted mode is the
/// authority and the server follows it.
enum ClientFloorMode: String, Equatable {
    case prayer, quiet, available

    static func of(_ mode: PortalMode) -> ClientFloorMode {
        if mode.isPraying { return .prayer }
        return mode.quiet ? .quiet : .available
    }
}

enum PortalEvent: Equatable {
    /// The talk tier reports bot speech started (`bot-started-speaking`); the turn id is minted here.
    case botStartedSpeaking(turnId: String)
    /// The talk tier reports bot speech stopped (`bot-stopped-speaking`).
    case botStoppedSpeaking
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
    /// Stop bot playback now (the active transport's `VoiceOutput`).
    case stopVoice
    /// Tell the talk server the phone's floor mode (`client_mode`).
    case sendClientMode(ClientFloorMode)
    case savePrayer(captureId: String)
    case waitForUser
}

/// Pure reducer. Tests cover every acceptance rule in the plan.
enum PortalReducer {
    static func apply(_ event: PortalEvent, to state: PortalMode) -> (PortalMode, [PortalEffect]) {
        var next = state
        var effects: [PortalEffect] = []
        switch event {
        case .botStartedSpeaking(let turnId):
            if SpeechGate.mayEnqueue(state) {
                if !state.isPraying { next.activity = .responding(turnId: turnId) }
            } else {
                // The server should be silent in prayer and quiet; if it speaks anyway, the phone
                // stops it again and re-asserts its mode. The gate is the phone's, not the server's.
                effects.append(.stopVoice)
                effects.append(.sendClientMode(ClientFloorMode.of(state)))
            }
        case .botStoppedSpeaking:
            if case .responding = state.activity { next.activity = .idle } // never to quiet
        case .stopRequested:
            effects.append(.stopVoice)
            if case .responding = state.activity { next.activity = .idle }
        case .enterPrayer(let captureId):
            // Entering prayer always silences current and queued speech, whatever was playing,
            // and tells the server before any of its own detection could.
            effects.append(.stopVoice)
            next.activity = .prayer(captureId: captureId)
            effects.append(.sendClientMode(.prayer))
        case .endPrayer:
            if case .prayer(let captureId) = state.activity {
                next.activity = .idle // quiet is untouched: pre-existing quiet remains
                effects.append(.savePrayer(captureId: captureId))
                effects.append(.waitForUser)
                effects.append(.sendClientMode(next.quiet ? .quiet : .available))
            }
        case .enterQuiet:
            next.quiet = true
            effects.append(.stopVoice)
            if case .responding = state.activity { next.activity = .idle }
            // While praying the server stays in prayer (nothing is spoken there either); quiet
            // is announced when the prayer ends.
            if !state.isPraying { effects.append(.sendClientMode(.quiet)) }
        case .wake:
            next.quiet = false
            if !state.isPraying { effects.append(.sendClientMode(.available)) }
        case .silence:
            break // silence never ends prayer, never changes mode, never produces output
        case .connectionLost, .connectionRestored:
            break // mode is never derived from connection lifetime
        case .relaunch:
            // A bot turn cannot survive the process; prayer and quiet always do.
            switch state.activity {
            case .listening, .responding: next.activity = .idle
            default: break
            }
        }
        return (next, effects)
    }
}
