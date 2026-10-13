"""UI evidence over WDA: preflight, attach without relaunch, and a navigation/keyboard/quiet smoke
scenario. Every step keeps the source XML and a screenshot; a step's claim is backed by the
element attributes WDA reports, never by the fact that a request returned 200.

Hard rules enforced here:
- never tap ``prayerButton``; an open prayer ("End prayer" label) blocks any scenario;
- never tap ``sendButton``, never press return, never type unless the caller passes explicit text
  (and even then only into ``typedInput``, dismissed with ``keyboardDone``, never submitted);
- never touch ``backendURLField`` beyond checking that it exists;
- never accept or dismiss alerts: an alert blocks the run and is photographed;
- the quiet flag is restored to what it was before the scenario.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .receipt import Receipt
from .wda import APP_STATE, Element, Wda, WdaError, element_summary, keyboard_visible, parse_source

IDS = {
    "status": "statusHeader",
    "prayer": "prayerButton",
    "quiet": "quietButton",
    "typed": "typedInput",
    "send": "sendButton",
    "keyboard_done": "keyboardDone",
    "backend_url": "backendURLField",
    "keyboard_done_diag": "keyboardDoneDiagnostics",
}
PRAYER_OPEN_LABEL = "End prayer"
PRAYER_IDLE_LABEL = "Begin prayer"
QUIET_LABELS = {"Quiet": False, "Wake": True}  # label shown -> quiet flag currently set
TABS = ("Portal", "Work", "Sources", "Diagnostics")


class Blocked(Exception):
    """A precondition outside this run's control; the receipt outcome is ``blocked``."""


def snapshot(wda: Wda, receipt: Receipt, out_dir: Path, tag: str) -> str:
    """Save source XML and PNG for ``tag``; returns the XML text."""
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_text = wda.source()
    xml_path = out_dir / f"{tag}.source.xml"
    xml_path.write_text(xml_text)
    receipt.evidence("source", xml_path, tag=tag)
    try:
        png = wda.screenshot()
        png_path = out_dir / f"{tag}.png"
        png_path.write_bytes(png)
        receipt.evidence("screenshot", png_path, tag=tag)
    except WdaError as exc:  # a missing screenshot is reported, not fatal for the XML evidence
        receipt.action("screenshot_failed", tag=tag, error=str(exc))
    return xml_text


def wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.3) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def require_no_alert(wda: Wda, receipt: Receipt, out_dir: Path, tag: str) -> None:
    text = wda.alert_text()
    receipt.action("alert_check", tag=tag, alert_text=text)
    if text:
        snapshot(wda, receipt, out_dir, f"{tag}-alert")
        raise Blocked(f"alert showing ({text[:120]!r}); not accepted or dismissed by this harness")


def require_unlocked(wda: Wda, receipt: Receipt) -> None:
    locked = wda.locked()
    receipt.action("lock_check", locked=locked)
    if locked:
        raise Blocked("device is locked; WDA cannot enter a passcode, unlock it (or disable auto-lock) and rerun")


def read_mode_labels(wda: Wda) -> dict[str, Any]:
    """Prayer and quiet button labels as WDA reports them (None when not on screen)."""
    out: dict[str, Any] = {}
    for key in ("prayer", "quiet"):
        el = wda.by_id_optional(IDS[key])
        out[key] = wda.label(el) if el else None
    return out


def require_no_open_prayer(labels: dict[str, Any], receipt: Receipt) -> None:
    prayer = labels.get("prayer")
    receipt.action("prayer_state", label=prayer)
    if prayer == PRAYER_OPEN_LABEL:
        raise Blocked("a prayer is open on the phone; this harness never ends it, rerun after it is ended by the user")


# ----- commands -----

def preflight(wda: Wda, bundle_id: str, receipt: Receipt, out_dir: Path) -> dict[str, Any]:
    """Server, lock, app state and on-screen prayer/quiet state. Opens and closes its own
    attach session; records the app state before anything else so a relaunch is impossible here."""
    status = wda.status()
    receipt.action("wda_status", ready=status.get("ready"), build=status.get("build"), os=status.get("os"), existing_session=status.get("sessionId"))
    receipt.check("wda_ready", bool(status.get("ready", True)), reason="WDA /status not ready")
    require_unlocked(wda, receipt)
    sid = wda.attach_session()
    receipt.action("attach_session", session_id=sid, capabilities="attach (no bundleId, shouldTerminateApp=false, forceAppLaunch=false)")
    result: dict[str, Any] = {"status": status}
    try:
        state = wda.app_state(bundle_id)
        receipt.action("app_state", bundle_id=bundle_id, state=state, meaning=APP_STATE.get(state, "?"))
        result["app_state"] = state
        receipt.check("app_running", state >= 2, reason=f"{bundle_id} is {APP_STATE.get(state)}; this harness does not launch it (a launch resets the audio owner); launch it on the phone first")
        require_no_alert(wda, receipt, out_dir, "preflight")
        if state == 4:
            xml = snapshot(wda, receipt, out_dir, "preflight")
            labels = read_mode_labels(wda)
            result["labels"] = labels
            receipt.action("mode_labels", **labels)
            require_no_open_prayer(labels, receipt)
            receipt.check("status_header_present", element_summary(parse_source(xml), IDS["status"]) is not None, reason="statusHeader not in source; the Portal tab is not on screen")
        else:
            receipt.action("app_not_foreground", note="labels not read; `session attach` activates without relaunch")
    finally:
        wda.delete_session(sid)
        receipt.action("delete_session", session_id=sid)
    return result


def attach(wda: Wda, bundle_id: str, receipt: Receipt, out_dir: Path) -> str:
    """Create an attach session, foreground the app with activate (never launch), and prove the
    process was already running before and is foreground after. Returns the session id (kept open)."""
    require_unlocked(wda, receipt)
    sid = wda.attach_session()
    receipt.action("attach_session", session_id=sid)
    before = wda.app_state(bundle_id)
    receipt.action("app_state_before", state=before, meaning=APP_STATE.get(before, "?"))
    if not receipt.check("app_was_running_before_activate", before >= 2, reason=f"{bundle_id} was {APP_STATE.get(before)}; activate would launch it, refusing"):
        wda.delete_session(sid)
        receipt.action("delete_session", session_id=sid, why="app not running")
        return sid
    if before != 4:
        wda.activate_app(bundle_id)
        receipt.action("activate_app", bundle_id=bundle_id)
    after = wda.app_state(bundle_id)
    receipt.action("app_state_after", state=after, meaning=APP_STATE.get(after, "?"))
    receipt.check("app_foreground_after", after == 4, reason=f"state {APP_STATE.get(after)}")
    receipt.check("no_relaunch_requested", all(not (e["path"] == "/session" and (e["payload"] or {}).get("desiredCapabilities", {}).get("bundleId")) for e in wda.log), reason="a /session with bundleId was sent")
    require_no_alert(wda, receipt, out_dir, "attach")
    xml = snapshot(wda, receipt, out_dir, "attach")
    receipt.check("status_header_present", element_summary(parse_source(xml), IDS["status"]) is not None, reason="statusHeader not in source")
    labels = read_mode_labels(wda)
    receipt.action("mode_labels", **labels)
    return sid


def release(wda: Wda, session_id: str, receipt: Receipt) -> None:
    wda.delete_session(session_id)
    receipt.action("delete_session", session_id=session_id)


def tap_tab(wda: Wda, name: str) -> Element:
    el = wda.find_optional("accessibility id", name)
    if el is None:
        el = wda.find("predicate string", f"label == '{name}' AND type == 'XCUIElementTypeButton'")
    wda.click(el)
    return el


def navigation_bar_present(wda: Wda, title: str) -> bool:
    return wda.find_optional("class chain", f'**/XCUIElementTypeNavigationBar[`name == "{title}"`]') is not None


def smoke(wda: Wda, bundle_id: str, receipt: Receipt, out_dir: Path, *, type_text: str | None = None, toggle_quiet: bool = True, step_timeout: float = 8.0) -> dict[str, Any]:
    """Navigation, keyboard and quiet-toggle smoke scenario with evidence per step.

    Steps: (1) Portal tab shows statusHeader, prayer and quiet labels read; (2) tap typedInput, the
    software keyboard must appear; (3) optional typing of ``type_text`` (never submitted);
    (4) tap keyboardDone, keyboard must be gone; (5) Diagnostics tab reachable, its navigation bar
    and backendURLField present (not touched); (6) back to Portal; (7) optional quiet toggle and
    restore. The prayer button is only ever read.
    """
    result: dict[str, Any] = {"steps": []}

    def step(name: str) -> None:
        result["steps"].append(name)
        receipt.action("step", step=name)

    require_unlocked(wda, receipt)
    require_no_alert(wda, receipt, out_dir, "smoke-start")
    state = wda.app_state(bundle_id)
    receipt.check("app_foreground_at_start", state == 4, reason=f"state {APP_STATE.get(state)}; run `session attach` first")
    if state != 4:
        return result

    step("portal_visible")
    xml = snapshot(wda, receipt, out_dir, "01-portal")
    root = parse_source(xml)
    receipt.check("status_header_present", element_summary(root, IDS["status"]) is not None, reason="statusHeader missing")
    labels = read_mode_labels(wda)
    receipt.action("mode_labels", **labels)
    require_no_open_prayer(labels, receipt)
    receipt.check("prayer_button_idle", labels.get("prayer") == PRAYER_IDLE_LABEL, reason=f"label {labels.get('prayer')!r}")
    quiet_before = QUIET_LABELS.get(labels.get("quiet") or "")
    receipt.check("quiet_label_known", quiet_before is not None, reason=f"label {labels.get('quiet')!r}")

    # Pre-existing draft: the placeholder is not a draft. A real draft is never modified; with
    # text to type it blocks the run before the field is even tapped.
    typed = wda.by_id(IDS["typed"])
    draft_before = wda.draft_text(typed)
    receipt.action("typed_draft_before", draft=draft_before, placeholder=wda.placeholder(typed))
    if type_text and draft_before:
        raise Blocked(f"typedInput already holds a draft ({draft_before[:60]!r}); refusing to type over it")
    typed_by_us = False

    try:
        step("tap_typed_input")
        wda.click(typed)
        appeared = wait_until(lambda: keyboard_visible(wda.source()), step_timeout)
        snapshot(wda, receipt, out_dir, "02-keyboard")
        receipt.check("keyboard_appeared", appeared, reason="XCUIElementTypeKeyboard not in source after tapping typedInput")

        if type_text:
            step("type_text")
            receipt.action("type_text", text=type_text, note="typed only; never submitted or sent; cleared again before this command returns")
            typed_by_us = True
            wda.type_text(type_text)
            value = wda.value(wda.by_id(IDS["typed"]))
            receipt.check("typed_value_matches", value == type_text, reason=f"field value {value!r}")
        else:
            receipt.action("type_text_skipped", note="no --type-text given; nothing typed")

        step("tap_keyboard_done")
        done = wda.by_id_optional(IDS["keyboard_done"])
        receipt.check("keyboard_done_present", done is not None, reason="keyboardDone toolbar button not found")
        if done is not None:
            wda.click(done)
            gone = wait_until(lambda: not keyboard_visible(wda.source()), step_timeout)
            snapshot(wda, receipt, out_dir, "03-after-done")
            receipt.check("keyboard_dismissed", gone, reason="keyboard still in source after keyboardDone")

        step("open_diagnostics")
        tap_tab(wda, "Diagnostics")
        nav = wait_until(lambda: navigation_bar_present(wda, "Diagnostics"), step_timeout)
        xml = snapshot(wda, receipt, out_dir, "04-diagnostics")
        receipt.check("diagnostics_navigation_bar", nav, reason="Diagnostics navigation bar not found")
        url_field = element_summary(parse_source(xml), IDS["backend_url"])
        receipt.check("backend_url_field_present", url_field is not None, reason="backendURLField not in source", summary=url_field, note="never tapped or edited")

        step("back_to_portal")
        tap_tab(wda, "Portal")
        back = wait_until(lambda: wda.by_id_optional(IDS["status"]) is not None, step_timeout)
        snapshot(wda, receipt, out_dir, "05-portal-again")
        receipt.check("portal_reachable_again", back, reason="statusHeader not found after tapping Portal")

        if toggle_quiet and quiet_before is not None:
            step("toggle_quiet")
            wda.click(wda.by_id(IDS["quiet"]))
            expected = "Quiet" if quiet_before else "Wake"
            flipped = wait_until(lambda: wda.label(wda.by_id(IDS["quiet"])) == expected, step_timeout)
            snapshot(wda, receipt, out_dir, "06-quiet-toggled")
            receipt.check("quiet_toggled", flipped, reason=f"label did not become {expected!r}")
        receipt.check("scenario_completed", True)
    except BaseException as exc:
        receipt.check("scenario_completed", False, reason=f"{exc.__class__.__name__}: {exc}")
        raise
    finally:
        # Each restoration is independent and recorded; failures here are checks, not exceptions,
        # so the original error (if any) is what the caller sees.
        _restore_typed_draft(wda, receipt, out_dir, typed_by_us=typed_by_us, draft_before=draft_before, step_timeout=step_timeout)
        if quiet_before is not None:
            result["quiet_restored"] = _restore_quiet(wda, receipt, out_dir, quiet_before=quiet_before, step_timeout=step_timeout)
        forbidden = {f"accessibility id={IDS['send']}", f"accessibility id={IDS['prayer']}", f"accessibility id={IDS['backend_url']}"}
        receipt.check("send_prayer_backend_never_tapped", not (forbidden & set(wda.clicked_locators())), clicked=wda.clicked_locators())
        try:
            final_labels = read_mode_labels(wda)
            receipt.action("mode_labels_final", **final_labels)
            receipt.check("prayer_untouched", final_labels.get("prayer") == labels.get("prayer"), reason="prayer label changed during the smoke scenario")
            result["labels_after"] = final_labels
        except WdaError as exc:
            receipt.check("prayer_untouched", False, reason=f"final labels unreadable: {exc}")
    result["labels_before"] = labels
    return result


def _restore_typed_draft(wda: Wda, receipt: Receipt, out_dir: Path, *, typed_by_us: bool, draft_before: str, step_timeout: float) -> None:
    """Clear only what this run typed into an initially empty field; a pre-existing draft is left
    exactly as found. The keyboard is put away in both cases."""
    # The draft is cleared through the element alone so a failing source dump cannot stop it.
    try:
        if typed_by_us:
            field = wda.by_id(IDS["typed"])
            if wda.draft_text(field) != "":
                wda.clear(field)
            remaining = wda.draft_text(wda.by_id(IDS["typed"]))
            receipt.check("own_typed_draft_cleared", remaining == "", reason=f"field still reads {remaining!r}; clear it by hand")
        else:
            remaining = wda.draft_text(wda.by_id(IDS["typed"]))
            receipt.check("preexisting_draft_preserved", remaining == draft_before, reason=f"draft changed from {draft_before!r} to {remaining!r}")
    except WdaError as exc:
        receipt.check("own_typed_draft_cleared" if typed_by_us else "preexisting_draft_preserved", False, reason=f"{exc}; inspect the field by hand")
    try:
        if keyboard_visible(wda.source()):
            done = wda.by_id_optional(IDS["keyboard_done"])
            if done is not None:
                wda.click(done)
            gone = wait_until(lambda: not keyboard_visible(wda.source()), step_timeout)
            receipt.check("keyboard_dismissed_at_end", gone, reason="keyboard still up after cleanup")
    except WdaError as exc:
        receipt.check("keyboard_dismissed_at_end", False, reason=f"{exc}; put the keyboard away by hand")
    try:
        snapshot(wda, receipt, out_dir, "08-typed-restored")
    except WdaError as exc:
        receipt.action("snapshot_failed", tag="08-typed-restored", error=str(exc))


def _restore_quiet(wda: Wda, receipt: Receipt, out_dir: Path, *, quiet_before: bool, step_timeout: float) -> bool:
    """Put the quiet flag back to what it was before the scenario, whatever happened in between."""
    original = "Wake" if quiet_before else "Quiet"
    try:
        current = wda.label(wda.by_id(IDS["quiet"]))
        if current != original:
            receipt.action("restore_quiet", from_label=current, to_label=original)
            wda.click(wda.by_id(IDS["quiet"]))
        restored = wait_until(lambda: wda.label(wda.by_id(IDS["quiet"])) == original, step_timeout)
        snapshot(wda, receipt, out_dir, "07-quiet-restored")
        receipt.check("quiet_restored", restored, reason=f"label is not back to {original!r}; restore it by hand")
        return restored
    except WdaError as exc:
        receipt.check("quiet_restored", False, reason=f"{exc}; restore it by hand")
        return False
