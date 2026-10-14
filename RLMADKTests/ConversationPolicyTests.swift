import XCTest
@testable import RLMADK

/// The phrase tables and the amen rule, pure: normalisation matches the talk server, `/policy`
/// overrides merge safely, and the floor decides on partials with the caller's clock.
final class ConversationPolicyTests: XCTestCase {
    private let policy = ConversationPolicy.defaults

    func testNormalisationFoldsPunctuationAndTypographicApostrophes() {
        XCTAssertEqual(FloorText.normalize("Let’s pray, Lord!"), "let's pray lord")
        XCTAssertEqual(FloorText.normalize("  In Jesus’ name,   Amen. "), "in jesus' name amen")
        XCTAssertEqual(FloorText.normalizeKeepingCommas("God, thank you"), "god, thank you")
        XCTAssertEqual(FloorText.words("Psalm 23, verse 1"), ["psalm", "verse"])
    }

    func testAddressPhrasesMatchLeadingWordsOnly() {
        XCTAssertEqual(policy.address(startOf: "Lord"), "lord")
        XCTAssertEqual(policy.address(startOf: "Lord, hear our prayer"), "lord")
        XCTAssertEqual(policy.address(startOf: "Dear Lord, thank you"), "dear lord")
        XCTAssertEqual(policy.address(startOf: "Heavenly Father we come"), "heavenly father")
        XCTAssertEqual(policy.address(startOf: "Let’s pray"), "let's pray")
        XCTAssertEqual(policy.address(startOf: "God, thank you for today"), "god,")
        XCTAssertNil(policy.address(startOf: "God is good"), "\"god,\" needs the comma")
        XCTAssertNil(policy.address(startOf: "Lordship and the Bible"))
        XCTAssertNil(policy.address(startOf: "What does Psalm 23 say about rest"))
        XCTAssertNil(policy.address(startOf: "I prayed yesterday"))
        XCTAssertNil(policy.address(startOf: "Father's Day plans"))
    }

    func testAmenAndCommandPhrases() {
        XCTAssertTrue(policy.endsWithAmen("and keep us safe, amen."))
        XCTAssertTrue(policy.endsWithAmen("In Jesus' name, Amen"))
        XCTAssertTrue(policy.endsWithAmen("Amen"))
        XCTAssertFalse(policy.endsWithAmen("amen and also my brother"))
        XCTAssertFalse(policy.endsWithAmen("Amenities nearby"))
        XCTAssertTrue(policy.isQuietCommand("Quiet please."))
        XCTAssertTrue(policy.isWakeCommand("Wake up, portal"))
        XCTAssertTrue(policy.isPrayerStartCommand("Begin prayer"))
        XCTAssertTrue(policy.isPrayerEndCommand("thank you amen end prayer"))
    }

    func testPolicyBodyMergesOverDefaultsAndRejectsBadValues() throws {
        let body = #"{"policy":{"address_phrases":["Abba","Lord"],"amen_silence_s":1.5,"wake_phrases":[],"prayer_preroll_s":99,"unknown":1}}"#
        let merged = try XCTUnwrap(ConversationPolicy.merged(from: Data(body.utf8)))
        XCTAssertEqual(merged.addressPhrases, ["abba", "lord"])
        XCTAssertEqual(merged.amenSilenceSeconds, 1.5)
        XCTAssertEqual(merged.wakePhrases, policy.wakePhrases, "an empty table never blanks the phone's")
        XCTAssertEqual(merged.prayerPrerollSeconds, policy.prayerPrerollSeconds, "out of range is ignored")
        XCTAssertEqual(merged.amenPhrases, policy.amenPhrases)
        // The talk server's TalkConfig shape (unwrapped) is accepted too.
        let flat = try XCTUnwrap(ConversationPolicy.merged(from: Data(#"{"amen_phrases":["amen","so be it"],"amen_silence_s":1.2}"#.utf8)))
        XCTAssertEqual(flat.amenPhrases, ["amen", "so be it"])
        XCTAssertNil(ConversationPolicy.merged(from: Data("[1,2]".utf8)))
    }

    func testPolicyCacheRoundTrip() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("policy-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        XCTAssertEqual(PolicyStore.load(root: root).source, "defaults")
        try Data(#"{"amen_silence_s":2.0}"#.utf8).write(to: PolicyStore.cacheURL(root: root))
        let loaded = PolicyStore.load(root: root)
        XCTAssertEqual(loaded.policy.amenSilenceSeconds, 2.0)
        XCTAssertTrue(loaded.source.hasPrefix("cached "))
    }

    // MARK: PrayerFloor (pure)

    private let t0 = Date(timeIntervalSince1970: 1_800_000_000)

    func testFloorEntersPrayerOnTheFirstPartialShowingAnAddress() {
        var floor = PrayerFloor()
        XCTAssertEqual(floor.observe(text: "What", final: false, praying: false, at: t0), .none)
        floor.newUtterance()
        XCTAssertEqual(floor.observe(text: "Lord", final: false, praying: false, at: t0), .enterPrayer(address: "lord"))
    }

    func testFloorAmenThenWindowEnds() {
        var floor = PrayerFloor()
        XCTAssertEqual(floor.observe(text: "Lord thank you", final: false, praying: true, at: t0), .none)
        XCTAssertEqual(floor.observe(text: "Lord thank you amen", final: false, praying: true, at: t0.addingTimeInterval(2)), .amenPending)
        XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(3.1)), .none)
        // A repeated partial with the same words is not new speech.
        XCTAssertEqual(floor.observe(text: "Lord, thank you, amen.", final: false, praying: true, at: t0.addingTimeInterval(3.15)), .none)
        XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(3.25)), .endPrayer)
        XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(9)), .none, "decided once")
    }

    func testFloorMoreWordsCancelThePendingEnd() {
        var floor = PrayerFloor()
        _ = floor.observe(text: "bless this home amen", final: false, praying: true, at: t0)
        XCTAssertEqual(floor.observe(text: "bless this home amen and my brother", final: false, praying: true, at: t0.addingTimeInterval(0.8)), .amenCancelled)
        XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(1.3)), .none)
        XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(60)), .none)
    }

    func testFloorSilenceAloneNeverEnds() {
        var floor = PrayerFloor()
        _ = floor.observe(text: "Lord I am here", final: false, praying: true, at: t0)
        for pause in [3.0, 4.5, 30, 600] {
            XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(pause), voice: VoiceFacts(active: false, lastVoiceEnd: t0)), .none, "pause \(pause) s")
        }
    }

    func testFloorAnchorsToVoiceEndOnlyWhenItBelongsToTheAmen() {
        var floor = PrayerFloor()
        _ = floor.observe(text: "keep us amen", final: false, praying: true, at: t0)
        // Speech ended 0.4 s before the partial arrived: the window counts from there.
        let voice = VoiceFacts(active: false, lastVoiceEnd: t0.addingTimeInterval(-0.4))
        XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(0.75), voice: voice), .none)
        XCTAssertEqual(floor.tick(praying: true, at: t0.addingTimeInterval(0.85), voice: voice), .endPrayer)
        // A voice end long before the amen is not its end: only the word rule applies.
        var other = PrayerFloor()
        _ = other.observe(text: "keep us amen", final: false, praying: true, at: t0)
        let stale = VoiceFacts(active: false, lastVoiceEnd: t0.addingTimeInterval(-5))
        XCTAssertEqual(other.tick(praying: true, at: t0.addingTimeInterval(0.9), voice: stale), .none)
        XCTAssertEqual(other.tick(praying: true, at: t0.addingTimeInterval(1.25), voice: stale), .endPrayer)
        // Voice still active never delays the word rule.
        var talking = PrayerFloor()
        _ = talking.observe(text: "keep us amen", final: false, praying: true, at: t0)
        XCTAssertEqual(talking.tick(praying: true, at: t0.addingTimeInterval(1.25), voice: VoiceFacts(active: true, lastVoiceEnd: nil)), .endPrayer)
    }
}
