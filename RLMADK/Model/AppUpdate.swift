import Foundation

/// The `latest.json` beside the private builds (`scripts/direct_install.py publish`): the newest
/// signed build and its install link.
struct LatestBuild: Codable, Equatable {
    var schema: String?
    var bundle_id: String?
    var version: String?
    var build: Int
    /// `itms-services://?action=download-manifest&url=...`: opening it shows Apple's install prompt.
    var install_url: String
    var page_url: String?
    var notes: String?
    var published_at: String?
}

/// The banner's install hand-off. iOS 26 queues an `itms-services` install opened from the running
/// app and waits ("Waiting…" on the Home Screen icon) until that app leaves the foreground; iOS 18
/// closed the app by itself. So when Apple's prompt closes, the app steps to the Home Screen at once,
/// where the icon shows the install, and the new build says "Updated to build N" on its first launch.
/// Nothing on the banner can be tapped meanwhile: on build 22 a tap on its ✕ one second after the
/// prompt closed cancelled the step aside and left the install queued for 25 minutes.
struct UpdateInstall: Equatable {
    enum Phase: String, Equatable {
        /// The install link was opened; Apple's prompt has not appeared yet.
        case opening
        /// Apple's prompt is showing (the app went inactive).
        case prompt
        /// The prompt closed; the app is stepping to the Home Screen.
        case leaving
        /// The app went to the Home Screen so iOS can replace it.
        case suspended
        /// The app came back to the foreground still on the old build (the prompt was cancelled).
        case returned
        /// No prompt appeared, or iOS refused the link.
        case noPrompt
        /// The app could not leave the foreground by itself.
        case closeManually
    }

    var build: Int
    var phase: Phase

    /// While true the banner shows only progress: no Install, no dismiss.
    var inFlight: Bool { [.opening, .prompt, .leaving, .suspended].contains(phase) }

    /// The banner's line for this phase.
    var message: String {
        switch phase {
        case .opening: return "Opening Apple's install prompt…"
        case .prompt: return "Tap Install in Apple's prompt."
        case .leaving: return "Installing: RLM-ADK steps aside so iOS can finish."
        case .suspended: return "Installing build \(build)…"
        case .returned: return "Build \(build) did not install. Tap Install to try again."
        case .noPrompt: return "No install prompt appeared. Open the install page instead."
        case .closeManually: return "Close RLM-ADK (swipe it away in the app switcher) so iOS can install build \(build)."
        }
    }
}

/// Decides whether the app offers an update (the update banner).
///
/// The app reads `latest.json` from the private install page (Tailscale only) when it comes to the
/// foreground and every few minutes while open. A newer build for this bundle is offered until it
/// is installed or dismissed; tapping Install opens its `itms-services` link, and iOS then asks
/// for the one confirmation it always asks for.
enum AppUpdate {
    /// The private install page's latest.json (Tailscale Serve on Spark-A).
    static let defaultURL = "https://rlm-adk-spark.tail0ec4a5.ts.net:8443/latest.json"
    /// Seconds between checks while the app is in the foreground.
    static let checkInterval: TimeInterval = 300

    /// Args:
    ///   latest: The published latest build.
    ///   currentBuild: This app's CFBundleVersion.
    ///   bundleId: This app's bundle identifier.
    ///   dismissedBuild: A build the user dismissed (not offered again).
    ///
    /// Returns:
    ///   The build to offer, or nil (not newer, another bundle, dismissed, or not an install link).
    static func offer(_ latest: LatestBuild, currentBuild: String, bundleId: String?, dismissedBuild: Int?) -> LatestBuild? {
        guard let current = Int(currentBuild), latest.build > current else { return nil }
        if let published = latest.bundle_id, let mine = bundleId, published != mine { return nil }
        if dismissedBuild == latest.build { return nil }
        guard let url = URL(string: latest.install_url), url.scheme == "itms-services" else { return nil }
        return latest
    }

    /// The Diagnostics line for one successful check.
    ///
    /// Args:
    ///   latest: The published build number.
    ///   currentBuild: This app's CFBundleVersion.
    ///   offered: Whether the banner now offers that build.
    ///   checkedAt: When the check finished.
    ///
    /// Returns:
    ///   For example "checked 08:34 · latest build 19 · up to date".
    static func statusLine(latest: Int, currentBuild: String, offered: Bool, checkedAt: Date) -> String {
        let outcome: String
        if let current = Int(currentBuild), latest > current {
            outcome = offered ? "build \(latest) offered" : "build \(latest) not offered (dismissed or another app)"
        } else {
            outcome = "up to date"
        }
        return "checked \(clock(checkedAt)) · latest build \(latest) · \(outcome)"
    }

    /// The first launch of a newer build: "Updated to build 21 (from 20)".
    ///
    /// Args:
    ///   lastLaunchedBuild: The build that last launched on this phone (nil before build 21).
    ///   currentBuild: This app's CFBundleVersion.
    ///
    /// Returns:
    ///   The notice, or nil when the build did not go up.
    static func updatedNotice(lastLaunchedBuild: Int?, currentBuild: String) -> String? {
        guard let last = lastLaunchedBuild, let current = Int(currentBuild), current > last else { return nil }
        return "Updated to build \(current) (from \(last))"
    }

    /// Local wall-clock time as HH:mm.
    static func clock(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }

    static func decode(_ data: Data) throws -> LatestBuild {
        try JSONDecoder().decode(LatestBuild.self, from: data)
    }
}
