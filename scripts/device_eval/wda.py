"""Small synchronous WebDriverAgent HTTP client (stdlib urllib) for a WDA already reachable at a
local URL (``pymobiledevice3 usbmux forward 8100 8100`` or any forwarder root chooses).

Only the routes this harness needs. Sessions are created in *attach* mode: no ``bundleId`` in the
capabilities, ``shouldTerminateApp`` and ``forceAppLaunch`` explicitly false, so WDA never
terminates or relaunches the app under evaluation. Foregrounding goes through
``/wda/apps/activate``, and the app's process state is read before and after through
``/wda/apps/state`` (1 not running, 2 background suspended, 3 background, 4 foreground).
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8100"
W3C_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"

APP_STATE = {0: "unknown", 1: "not_running", 2: "background_suspended", 3: "background", 4: "foreground"}

# Capabilities that make a session attach instead of launching. Both names are sent because WDA
# versions differ on which one they honour; neither may ever be true in this harness.
ATTACH_CAPABILITIES: dict[str, Any] = {
    "shouldTerminateApp": False,
    "forceAppLaunch": False,
    "shouldWaitForQuiescence": False,
}


class WdaError(Exception):
    def __init__(self, message: str, status_code: int | None = None, error: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error = error  # WDA/W3C error string such as "no such element", "no such alert"


@dataclass
class Element:
    id: str
    using: str
    value: str


@dataclass
class Wda:
    base_url: str = DEFAULT_URL
    timeout: float = 15.0
    session_id: str | None = None
    # Every request made, for the receipt: method, path, payload (secrets never pass through here).
    log: list[dict[str, Any]] = field(default_factory=list)
    # element id -> locator it was found by, so clicks can be audited by identifier
    found: dict[str, str] = field(default_factory=dict)

    # ----- transport -----

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=body, method=method)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        self.log.append({"method": method, "path": path, "payload": payload})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                code = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            code = exc.code
        except (urllib.error.URLError, OSError) as exc:
            raise WdaError(f"WDA unreachable at {url}: {exc}") from exc
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError as exc:
            raise WdaError(f"WDA returned non-JSON (HTTP {code}) for {method} {path}", status_code=code) from exc
        value = data.get("value")
        err = value.get("error") if isinstance(value, dict) else None
        if code >= 400 or err or data.get("status") not in (None, 0, "0"):
            msg = value.get("message") if isinstance(value, dict) else value
            raise WdaError(f"WDA error HTTP {code} for {method} {path}: {err or ''} {msg or ''}".strip(), status_code=code, error=err)
        return data

    def _sid(self, session_id: str | None = None) -> str:
        sid = session_id or self.session_id
        if not sid:
            raise WdaError("no session; run `session attach` first or pass --session-id")
        return sid

    # ----- server and device state -----

    def status(self) -> dict[str, Any]:
        return self.request("GET", "/status").get("value") or {}

    def locked(self) -> bool:
        """Screen lock state. The session-less route is tried first, then the session route."""
        try:
            data = self.request("GET", "/wda/locked")
        except WdaError as exc:
            if exc.status_code != 404 or not self.session_id:
                raise
            data = self.request("GET", f"/session/{self.session_id}/wda/locked")
        return bool(data.get("value"))

    # ----- sessions -----

    def attach_session(self, extra_capabilities: dict[str, Any] | None = None) -> str:
        """Create a session that launches nothing. ``bundleId`` is refused on purpose."""
        caps = dict(ATTACH_CAPABILITIES)
        caps.update(extra_capabilities or {})
        if caps.get("bundleId"):
            raise WdaError("attach_session refuses bundleId: a bundleId capability makes WDA launch the app")
        if caps.get("shouldTerminateApp") or caps.get("forceAppLaunch"):
            raise WdaError("attach_session refuses shouldTerminateApp/forceAppLaunch=true")
        payload = {"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}, "desiredCapabilities": caps}
        data = self.request("POST", "/session", payload)
        sid = data.get("sessionId") or (data.get("value") or {}).get("sessionId")
        if not sid:
            raise WdaError("WDA returned no session id")
        self.session_id = sid
        return sid

    def delete_session(self, session_id: str | None = None) -> None:
        sid = self._sid(session_id)
        self.request("DELETE", f"/session/{sid}")
        if sid == self.session_id:
            self.session_id = None

    # ----- apps -----

    def app_state(self, bundle_id: str) -> int:
        data = self.request("POST", f"/session/{self._sid()}/wda/apps/state", {"bundleId": bundle_id})
        return int(data.get("value") or 0)

    def activate_app(self, bundle_id: str) -> None:
        self.request("POST", f"/session/{self._sid()}/wda/apps/activate", {"bundleId": bundle_id})

    def active_app_info(self) -> dict[str, Any]:
        return self.request("GET", f"/session/{self._sid()}/wda/activeAppInfo").get("value") or {}

    # ----- elements -----

    def find(self, using: str, value: str) -> Element:
        data = self.request("POST", f"/session/{self._sid()}/element", {"using": using, "value": value})
        el = data.get("value") or {}
        eid = el.get("ELEMENT") or el.get(W3C_ELEMENT_KEY) or el.get("element") if isinstance(el, dict) else None
        if not eid:
            raise WdaError(f"no element id returned for {using}={value!r}", error="no such element")
        self.found[eid] = f"{using}={value}"
        return Element(id=eid, using=using, value=value)

    def find_optional(self, using: str, value: str) -> Element | None:
        try:
            return self.find(using, value)
        except WdaError as exc:
            if exc.error == "no such element" or exc.status_code == 404:
                return None
            raise

    def by_id(self, identifier: str) -> Element:
        return self.find("accessibility id", identifier)

    def by_id_optional(self, identifier: str) -> Element | None:
        return self.find_optional("accessibility id", identifier)

    def attribute(self, el: Element, name: str) -> Any:
        return self.request("GET", f"/session/{self._sid()}/element/{el.id}/attribute/{name}").get("value")

    def label(self, el: Element) -> str:
        return str(self.attribute(el, "label") or "")

    def displayed(self, el: Element) -> bool:
        return bool(self.request("GET", f"/session/{self._sid()}/element/{el.id}/displayed").get("value"))

    def enabled(self, el: Element) -> bool:
        return bool(self.request("GET", f"/session/{self._sid()}/element/{el.id}/enabled").get("value"))

    def rect(self, el: Element) -> dict[str, Any]:
        return self.request("GET", f"/session/{self._sid()}/element/{el.id}/rect").get("value") or {}

    def value(self, el: Element) -> str:
        v = self.attribute(el, "value")
        return "" if v is None else str(v)

    def placeholder(self, el: Element) -> str:
        v = self.attribute(el, "placeholderValue")
        return "" if v is None else str(v)

    def draft_text(self, el: Element) -> str:
        """Actual text in a text field: XCUITest reports the placeholder as ``value`` when the
        field is empty, so a value equal to the placeholder is no draft."""
        value = self.value(el)
        return "" if value == self.placeholder(el) else value

    def clear(self, el: Element) -> None:
        self.request("POST", f"/session/{self._sid()}/element/{el.id}/clear", {})
        self.log[-1]["cleared"] = self.found.get(el.id, f"{el.using}={el.value}")

    def click(self, el: Element) -> None:
        self.request("POST", f"/session/{self._sid()}/element/{el.id}/click", {})
        self.log[-1]["clicked"] = self.found.get(el.id, f"{el.using}={el.value}")

    def clicked_locators(self) -> list[str]:
        return [e["clicked"] for e in self.log if "clicked" in e]

    def type_text(self, text: str) -> None:
        """Type into the focused element. The caller decides whether typing is allowed at all."""
        self.request("POST", f"/session/{self._sid()}/wda/keys", {"value": list(text)})

    # ----- evidence -----

    def source(self) -> str:
        data = self.request("GET", f"/session/{self._sid()}/source")
        value = data.get("value")
        if not isinstance(value, str):
            raise WdaError("WDA returned no source string")
        return value

    def screenshot(self) -> bytes:
        data = self.request("GET", f"/session/{self._sid()}/screenshot")
        value = data.get("value")
        if not isinstance(value, str):
            raise WdaError("WDA returned no screenshot")
        return base64.b64decode(value)

    def alert_text(self) -> str | None:
        """Text of a system alert if one is showing, else None. Never accepts or dismisses."""
        try:
            data = self.request("GET", f"/session/{self._sid()}/alert/text")
        except WdaError as exc:
            if exc.error == "no such alert" or exc.status_code == 404:
                return None
            raise
        value = data.get("value")
        return str(value) if value else None


# ----- source XML helpers -----

def parse_source(xml_text: str) -> ET.Element:
    return ET.fromstring(xml_text)


def iter_elements(root: ET.Element, type_name: str | None = None):
    for node in root.iter():
        if type_name is None or node.tag == type_name or node.get("type") == type_name:
            yield node


def keyboard_visible(xml_text: str) -> bool:
    root = parse_source(xml_text)
    for node in iter_elements(root, "XCUIElementTypeKeyboard"):
        if node.get("visible", "true").lower() != "false":
            return True
    return False


def element_summary(root: ET.Element, identifier: str) -> dict[str, Any] | None:
    """Attributes of the first element whose name (accessibility identifier) matches."""
    for node in root.iter():
        if node.get("name") == identifier:
            return {k: node.get(k) for k in ("type", "name", "label", "value", "enabled", "visible", "accessible", "x", "y", "width", "height") if node.get(k) is not None}
    return None
