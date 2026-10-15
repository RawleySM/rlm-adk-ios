import XCTest

/// The keyboard must never trap the user: after typing, the keyboard toolbar's Done, submit, and
/// the mode buttons put it away, and the bottom tabs are reachable again. Runs on the simulator with
/// no backend (unreachable URL) and no permission prompts; UI evidence, not device-voice proof.
final class KeyboardNavigationUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUp() {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchEnvironment["RLMADK_SKIP_PERMISSION_PROMPTS"] = "1"
        app.launchEnvironment["RLMADK_BACKEND_URL"] = "http://127.0.0.1:1"
        app.launch()
    }

    /// Focuses the typed input field and REQUIRES the software keyboard: this is the reported bug,
    /// so a run where no keyboard appears (a hardware keyboard attached to the simulator) fails
    /// instead of passing silently. CI disables the hardware keyboard before booting the simulator.
    private func focusTypedInput() {
        let field = app.textFields["typedInput"]
        XCTAssertTrue(field.waitForExistence(timeout: 10), "typed input field present")
        if !field.isHittable { app.swipeUp() }
        field.tap()
        XCTAssertTrue(app.keyboards.element.waitForExistence(timeout: 5), "software keyboard must appear after tapping the typed input field (is a hardware keyboard connected to the simulator?)")
    }

    private func assertKeyboardGone(_ why: String) {
        let gone = NSPredicate(format: "count == 0")
        expectation(for: gone, evaluatedWith: app.keyboards, handler: nil)
        waitForExpectations(timeout: 5)
        XCTAssertEqual(app.keyboards.count, 0, "keyboard still up after \(why)")
    }

    func testDoneButtonDismissesKeyboardAndTabsAreReachable() throws {
        focusTypedInput()
        app.textFields["typedInput"].typeText("hello")
        let done = app.buttons["keyboardDone"]
        XCTAssertTrue(done.waitForExistence(timeout: 3), "keyboard toolbar Done present")
        done.tap()
        assertKeyboardGone("Done")
        let diagnosticsTab = app.tabBars.buttons["Diagnostics"]
        XCTAssertTrue(diagnosticsTab.waitForExistence(timeout: 5))
        XCTAssertTrue(diagnosticsTab.isHittable, "Diagnostics tab reachable after Done")
        diagnosticsTab.tap()
        XCTAssertTrue(app.navigationBars["Diagnostics"].waitForExistence(timeout: 5), "Diagnostics opened")
        // The Diagnostics URL field has its own Done; the Portal tab is reachable afterwards.
        let urlField = app.textFields["backendURLField"]
        XCTAssertTrue(urlField.waitForExistence(timeout: 5))
        urlField.tap()
        XCTAssertTrue(app.keyboards.element.waitForExistence(timeout: 5), "software keyboard for the URL field")
        app.buttons["keyboardDoneDiagnostics"].tap()
        assertKeyboardGone("Diagnostics Done")
        XCTAssertTrue(app.tabBars.buttons["Portal"].isHittable)
        app.tabBars.buttons["Portal"].tap()
        // The portal page hides its navigation bar (the scripture pane takes the space); its own controls mark it.
        XCTAssertTrue(app.buttons["passagePill"].waitForExistence(timeout: 5), "Portal page with the scripture pane opened")
        XCTAssertTrue(app.textFields["typedInput"].exists)
    }

    func testQuietButtonDismissesKeyboardAndStillTogglesQuiet() throws {
        focusTypedInput()
        app.textFields["typedInput"].typeText("quiet")
        let quiet = app.buttons["quietButton"]
        XCTAssertTrue(quiet.waitForExistence(timeout: 5))
        if !quiet.isHittable { app.swipeDown() }
        XCTAssertTrue(quiet.isHittable, "Quiet button must stay reachable while typing")
        quiet.tap()
        assertKeyboardGone("Quiet")
        let woke = NSPredicate(format: "label == 'Wake'")
        expectation(for: woke, evaluatedWith: quiet, handler: nil)
        waitForExpectations(timeout: 5)
        quiet.tap() // wake again; the keyboard stays down
        XCTAssertEqual(app.keyboards.count, 0)
        XCTAssertTrue(app.tabBars.buttons["Diagnostics"].isHittable)
    }

    func testSubmitSendsAndDismissesKeyboard() throws {
        focusTypedInput()
        app.textFields["typedInput"].typeText("What does the shepherd psalm say?\n")
        assertKeyboardGone("submit")
        XCTAssertTrue(app.staticTexts["You: What does the shepherd psalm say?"].waitForExistence(timeout: 5), "typed turn recorded as the last transcript")
        XCTAssertTrue(app.tabBars.buttons["Work"].isHittable)
    }

    func testSendButtonDismissesKeyboard() throws {
        focusTypedInput()
        app.textFields["typedInput"].typeText("send me")
        let send = app.buttons["sendButton"]
        XCTAssertTrue(send.waitForExistence(timeout: 3))
        send.tap()
        assertKeyboardGone("Send")
        XCTAssertTrue(app.staticTexts["You: send me"].waitForExistence(timeout: 5))
    }
}
