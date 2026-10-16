import XCTest
@testable import RLMADK

/// The update banner: latest.json decoding, which build is offered, and the model's check,
/// install and dismiss.
@MainActor
final class AppUpdateTests: XCTestCase {
    private let json = #"{"schema":"rlmadk.latest_build.v1","bundle_id":"com.rawleystanhope.rlmadk","version":"0.2.0","build":20,"install_url":"itms-services://?action=download-manifest&url=https%3A%2F%2Fhost%2Fbuild20%2Fmanifest.plist","page_url":"https://host/build20/","notes":"Adds X.","published_at":"2026-10-16T00:00:00+00:00"}"#
    private var roots: [URL] = []

    override func tearDown() {
        UserDefaults.standard.removeObject(forKey: "dismissedUpdateBuild")
        for root in roots { try? FileManager.default.removeItem(at: root) }
        roots = []
        super.tearDown()
    }

    func testDecodesLatestJSON() throws {
        let latest = try AppUpdate.decode(Data(json.utf8))
        XCTAssertEqual(latest.build, 20)
        XCTAssertEqual(latest.notes, "Adds X.")
        XCTAssertTrue(latest.install_url.hasPrefix("itms-services://"))
    }

    func testOfferRules() throws {
        let latest = try AppUpdate.decode(Data(json.utf8))
        let bundle = "com.rawleystanhope.rlmadk"
        XCTAssertEqual(AppUpdate.offer(latest, currentBuild: "19", bundleId: bundle, dismissedBuild: nil)?.build, 20)
        XCTAssertNil(AppUpdate.offer(latest, currentBuild: "20", bundleId: bundle, dismissedBuild: nil), "not newer")
        XCTAssertNil(AppUpdate.offer(latest, currentBuild: "21", bundleId: bundle, dismissedBuild: nil), "older")
        XCTAssertNil(AppUpdate.offer(latest, currentBuild: "19", bundleId: "other.app", dismissedBuild: nil), "another bundle")
        XCTAssertNil(AppUpdate.offer(latest, currentBuild: "19", bundleId: bundle, dismissedBuild: 20), "dismissed")
        XCTAssertEqual(AppUpdate.offer(latest, currentBuild: "19", bundleId: bundle, dismissedBuild: 18)?.build, 20, "an older dismissal does not hide a newer build")
        var web = latest
        web.install_url = "https://host/build20/"
        XCTAssertNil(AppUpdate.offer(web, currentBuild: "19", bundleId: bundle, dismissedBuild: nil), "only install links are offered")
        XCTAssertNil(AppUpdate.offer(latest, currentBuild: "?", bundleId: bundle, dismissedBuild: nil), "unknown own build")
    }

    private func makeModel() -> PortalModel {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("update-\(UUID().uuidString)")
        roots.append(root)
        return PortalModel(store: LocalStore(root: root))
    }

    func testModelOffersInstallsAndDismisses() async throws {
        let model = makeModel()
        // No bundle_id: the test bundle's identifier need not be the app's.
        let body = json.replacingOccurrences(of: #""build":20"#, with: #""build":99999"#).replacingOccurrences(of: #""bundle_id":"com.rawleystanhope.rlmadk","#, with: "")
        model.updateFetcher = { _ in Data(body.utf8) }
        var opened: [URL] = []
        model.urlOpener = { opened.append($0) }
        await model.checkForUpdate(reason: "test")
        XCTAssertEqual(model.updateOffer?.build, 99999)
        XCTAssertTrue(model.updateCheckStatus.hasSuffix("latest build 99999 · build 99999 offered"), model.updateCheckStatus)
        model.installUpdate()
        XCTAssertEqual(opened.first?.scheme, "itms-services")
        model.dismissUpdate()
        XCTAssertNil(model.updateOffer)
        await model.checkForUpdate(reason: "test")
        XCTAssertNil(model.updateOffer, "a dismissed build is not offered again")
    }

    func testFailedCheckKeepsNoOffer() async {
        let model = makeModel()
        model.updateFetcher = { _ in throw URLError(.notConnectedToInternet) }
        await model.checkForUpdate(reason: "test")
        XCTAssertNil(model.updateOffer)
        XCTAssertTrue(model.updateCheckStatus.hasPrefix("check failed "), model.updateCheckStatus)
    }

    func testStatusLine() {
        let at = Date()
        let clock = AppUpdate.clock(at)
        XCTAssertEqual(AppUpdate.statusLine(latest: 19, currentBuild: "19", offered: false, checkedAt: at), "checked \(clock) · latest build 19 · up to date")
        XCTAssertEqual(AppUpdate.statusLine(latest: 20, currentBuild: "19", offered: true, checkedAt: at), "checked \(clock) · latest build 20 · build 20 offered")
        XCTAssertEqual(AppUpdate.statusLine(latest: 20, currentBuild: "19", offered: false, checkedAt: at), "checked \(clock) · latest build 20 · build 20 not offered (dismissed or another app)")
    }
}
