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

    static func decode(_ data: Data) throws -> LatestBuild {
        try JSONDecoder().decode(LatestBuild.self, from: data)
    }
}
