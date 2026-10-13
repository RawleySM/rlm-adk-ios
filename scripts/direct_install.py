#!/usr/bin/env python3
"""Direct private delivery of the signed iPhone app (Ad Hoc / release-testing), without TestFlight.

One stdlib utility, used by .github/workflows/direct-install.yml and testable on Linux:

  validate-profile   decoded provisioning-profile plist (security cms -D output on macOS) -> JSON
                     summary; exit 1 with the reason when the profile is not an Ad Hoc profile for
                     this team, bundle and the exact requested device.
  select-method      pick the export method the runner's `xcodebuild -help` text accepts
                     (release-testing, else ad-hoc), exit 1 when neither is listed.
  export-options     write the export options plist for that method and the validated profile name.
  inspect-ipa        identity of the exported package from its own Info.plist, plus sha256 and size.
  receipt            cross-check the exported package (Info.plist, embedded profile summary,
                     codesign output, entitlements) against the validated profile and the requested
                     build; write the receipt only when everything agrees.
  package            manifest.plist, index.html and the sha256 sidecar for a caller-provided HTTPS
                     base URL, identity read from the package.

Nothing here decodes CMS, signs, uploads to Apple, or installs on a phone.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import plistlib
import re
import sys
import urllib.parse
import zipfile
from pathlib import Path

TEAM = "4PJBKY2WQR"
BUNDLE = "com.rawleystanhope.rlmadk"
METHODS = ("release-testing", "ad-hoc")  # newer name first; both mean registered-device distribution


class Rejected(Exception):
    """A validation failure with the reason the workflow prints."""


# ----- provisioning profile (decoded plist) -----

def load_plist(data: bytes) -> dict:
    try:
        obj = plistlib.loads(data)
    except Exception as exc:  # plistlib raises several types for malformed input
        raise Rejected(f"not a readable plist: {exc.__class__.__name__}: {exc}") from exc
    if not isinstance(obj, dict):
        raise Rejected("plist root is not a dictionary")
    return obj


def validate_profile(plist: dict, *, udid: str, team: str = TEAM, bundle: str = BUNDLE, now: dt.datetime | None = None) -> dict:
    """Summary of an Ad Hoc profile that covers exactly the requested device, or Rejected."""
    now = now or dt.datetime.now(dt.timezone.utc)
    name = plist.get("Name")
    uuid = plist.get("UUID")
    if not name or not uuid:
        raise Rejected("profile has no Name/UUID")
    if plist.get("ProvisionsAllDevices"):
        raise Rejected("ProvisionsAllDevices is set: this is an enterprise (in-house) profile, not Ad Hoc")
    devices = plist.get("ProvisionedDevices")
    if not isinstance(devices, list) or not devices:
        raise Rejected("no ProvisionedDevices: this is an App Store profile (or empty), it cannot install directly on a phone")
    teams = plist.get("TeamIdentifier") or []
    if team not in teams:
        raise Rejected(f"TeamIdentifier {teams} does not include {team}")
    ent = plist.get("Entitlements") or {}
    app_id = ent.get("application-identifier")
    if app_id != f"{team}.{bundle}":
        raise Rejected(f"application-identifier {app_id!r} is not {team}.{bundle}")
    if ent.get("get-task-allow"):
        raise Rejected("get-task-allow is true: this is a development (debugging) profile, not Ad Hoc")
    if ent.get("beta-reports-active"):
        raise Rejected("beta-reports-active is set: App Store / TestFlight profile")
    expiry = plist.get("ExpirationDate")
    if not isinstance(expiry, dt.datetime):
        raise Rejected("ExpirationDate missing")
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=dt.timezone.utc)
    days_left = (expiry - now).total_seconds() / 86400
    if days_left <= 0:
        raise Rejected(f"profile expired at {expiry.isoformat()}")
    wanted = (udid or "").strip().lower()
    if not wanted:
        raise Rejected("no target device UDID given")
    listed = [str(d) for d in devices]
    if wanted not in [d.lower() for d in listed]:
        raise Rejected(f"requested device {udid} is not in ProvisionedDevices ({len(listed)} device(s): {', '.join(listed)})")
    certs = plist.get("DeveloperCertificates") or []
    fingerprints = [hashlib.sha256(bytes(c)).hexdigest() for c in certs]
    return {
        "name": name,
        "uuid": uuid,
        "team": team,
        "application_identifier": app_id,
        "platform": plist.get("Platform"),
        "expires_at": expiry.isoformat(),
        "days_left": round(days_left, 1),
        "near_expiry": days_left < 30,  # informational only
        "devices": listed,
        "target_udid": udid,
        "certificate_sha256": fingerprints,
        "get_task_allow": bool(ent.get("get-task-allow")),
    }


# ----- export method and options -----

def select_method(help_text: str) -> str:
    for method in METHODS:
        if re.search(rf"(?<![\w-]){re.escape(method)}(?![\w-])", help_text):
            return method
    raise Rejected(f"xcodebuild -help lists neither {' nor '.join(METHODS)} as an export method")


def export_options(method: str, profile_name: str, *, team: str = TEAM, bundle: str = BUNDLE) -> bytes:
    if method not in METHODS:
        raise Rejected(f"unsupported export method {method!r}")
    return plistlib.dumps({
        "method": method,
        "destination": "export",
        "teamID": team,
        "signingStyle": "manual",
        "signingCertificate": "Apple Distribution",
        "provisioningProfiles": {bundle: profile_name},
        "thinning": "<none>",
    }, sort_keys=True)


# ----- exported package -----

def app_info_from_ipa(ipa: Path) -> tuple[str, dict]:
    """(app directory name, Info.plist dict) from Payload/<one>.app/Info.plist."""
    with zipfile.ZipFile(ipa) as z:
        apps = sorted({n.split("/")[1] for n in z.namelist() if n.startswith("Payload/") and n.count("/") >= 2 and n.split("/")[1].endswith(".app")})
        if len(apps) != 1:
            raise Rejected(f"expected exactly one .app under Payload/, found {apps}")
        try:
            info = load_plist(z.read(f"Payload/{apps[0]}/Info.plist"))
        except KeyError as exc:
            raise Rejected(f"Payload/{apps[0]}/Info.plist missing") from exc
    return apps[0], info


def inspect_ipa(ipa: Path) -> dict:
    app, info = app_info_from_ipa(ipa)
    data = ipa.read_bytes()
    return {
        "ipa": ipa.name,
        "app": app,
        "bundle_id": info.get("CFBundleIdentifier"),
        "version": info.get("CFBundleShortVersionString"),
        "build": info.get("CFBundleVersion"),
        "display_name": info.get("CFBundleDisplayName") or info.get("CFBundleName"),
        "minimum_os": info.get("MinimumOSVersion"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
    }


def parse_codesign(text: str) -> dict:
    """Key=value lines from `codesign -dvvv` (Identifier, TeamIdentifier, Authority, ...)."""
    out: dict = {"authorities": []}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "Authority":
            out["authorities"].append(value)
        elif key not in out:
            out[key] = value
    return out


def build_receipt(*, ipa: Path, profile: dict, embedded: dict, codesign_text: str, entitlements: dict, commit: str, method: str, build_number: str, bundle: str = BUNDLE, team: str = TEAM) -> dict:
    identity = inspect_ipa(ipa)
    problems = []
    if identity["bundle_id"] != bundle:
        problems.append(f"package bundle id {identity['bundle_id']!r} is not {bundle}")
    if str(identity["build"]) != str(build_number):
        problems.append(f"package CFBundleVersion {identity['build']!r} is not the requested build {build_number!r}")
    if embedded.get("uuid") != profile.get("uuid"):
        problems.append(f"embedded profile {embedded.get('uuid')} differs from the validated profile {profile.get('uuid')}")
    if embedded.get("devices") != profile.get("devices"):
        problems.append("embedded profile device list differs from the validated profile")
    cs = parse_codesign(codesign_text)
    if cs.get("Identifier") != bundle:
        problems.append(f"codesign Identifier {cs.get('Identifier')!r} is not {bundle}")
    if cs.get("TeamIdentifier") != team:
        problems.append(f"codesign TeamIdentifier {cs.get('TeamIdentifier')!r} is not {team}")
    if not any(a.startswith("Apple Distribution") for a in cs["authorities"]):
        problems.append(f"codesign authority is not Apple Distribution: {cs['authorities']}")
    if entitlements.get("application-identifier") != f"{team}.{bundle}":
        problems.append(f"entitlement application-identifier {entitlements.get('application-identifier')!r} is not {team}.{bundle}")
    if entitlements.get("get-task-allow"):
        problems.append("entitlement get-task-allow is true (debuggable build, not release-testing)")
    if entitlements.get("beta-reports-active"):
        problems.append("entitlement beta-reports-active is set (TestFlight build)")
    if problems:
        raise Rejected("; ".join(problems))
    return {
        "kind": "direct-install-receipt",
        "commit": commit,
        "export_method": method,
        "package": identity,
        "signing": {"identifier": cs.get("Identifier"), "team": cs.get("TeamIdentifier"), "authorities": cs["authorities"], "cdhash": cs.get("CDHash"), "entitlements": {k: entitlements[k] for k in sorted(entitlements)}},
        "profile": profile,
        "installs_on": profile["devices"],
        "expires_at": profile["expires_at"],
        "note": "Signed for the listed registered device(s) only; Developer Mode on the phone and Apple's first-launch certificate check apply. No upload to Apple was made.",
    }


# ----- install manifest and page -----

def package(ipa: Path, base_url: str, out: Path, *, title: str | None = None) -> dict:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise Rejected(f"base URL must be https://host/path, got {base_url!r} (itms-services needs a trusted HTTPS manifest)")
    identity = inspect_ipa(ipa)
    if not identity["bundle_id"] or not identity["build"]:
        raise Rejected("package Info.plist lacks CFBundleIdentifier or CFBundleVersion")
    base = base_url.rstrip("/") + "/"
    ipa_name = ipa.name
    title = title or identity["display_name"] or identity["bundle_id"]
    ipa_url = urllib.parse.urljoin(base, urllib.parse.quote(ipa_name))
    manifest_url = urllib.parse.urljoin(base, "manifest.plist")
    manifest = {
        "items": [{
            "assets": [{"kind": "software-package", "url": ipa_url}],
            "metadata": {"bundle-identifier": identity["bundle_id"], "bundle-version": str(identity["build"]), "kind": "software", "title": title},
        }]
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.plist").write_bytes(plistlib.dumps(manifest, sort_keys=True))
    link = "itms-services://?action=download-manifest&url=" + urllib.parse.quote(manifest_url, safe="")
    page = f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} {html.escape(str(identity['version']))} ({html.escape(str(identity['build']))})</title>
<h1>{html.escape(title)}</h1>
<p>Version {html.escape(str(identity['version']))}, build {html.escape(str(identity['build']))}, bundle {html.escape(identity['bundle_id'])}.</p>
<p><a href="{html.escape(link, quote=True)}">Install on this iPhone</a></p>
<p>Private signed build for registered devices only. Developer Mode must be on; the first launch needs internet for Apple's certificate check.</p>
<p>IPA sha256 <code>{identity['sha256']}</code>, {identity['size_bytes']} bytes.</p>
"""
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / f"{ipa_name}.sha256").write_text(f"{identity['sha256']}  {ipa_name}\n", encoding="utf-8")
    return {"ipa_url": ipa_url, "manifest_url": manifest_url, "install_link": link, "identity": identity, "files": ["manifest.plist", "index.html", f"{ipa_name}.sha256"]}


# ----- CLI -----

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("validate-profile"); a.add_argument("--plist", required=True); a.add_argument("--udid", required=True); a.add_argument("--json", required=True)
    a = sub.add_parser("select-method"); a.add_argument("--help-text", required=True)
    a = sub.add_parser("export-options"); a.add_argument("--method", required=True); a.add_argument("--profile-name", required=True); a.add_argument("--out", required=True)
    a = sub.add_parser("inspect-ipa"); a.add_argument("--ipa", required=True)
    a = sub.add_parser("receipt")
    for flag in ("--ipa", "--profile-json", "--embedded-profile-json", "--codesign-text", "--entitlements-plist", "--commit", "--method", "--build-number", "--out"):
        a.add_argument(flag, required=True)
    a = sub.add_parser("package"); a.add_argument("--ipa", required=True); a.add_argument("--base-url", required=True); a.add_argument("--out", required=True); a.add_argument("--title")
    args = p.parse_args(argv)
    try:
        if args.cmd == "validate-profile":
            summary = validate_profile(load_plist(Path(args.plist).read_bytes()), udid=args.udid)
            Path(args.json).write_text(json.dumps(summary, indent=2) + "\n")
            print(f"profile OK: {summary['name']} ({summary['uuid']}) expires {summary['expires_at']} ({summary['days_left']} days), devices {len(summary['devices'])}, target {summary['target_udid']} included")
            if summary["near_expiry"]:
                print("note: profile expires within 30 days")
        elif args.cmd == "select-method":
            print(select_method(Path(args.help_text).read_text(errors="replace")))
        elif args.cmd == "export-options":
            Path(args.out).write_bytes(export_options(args.method, args.profile_name))
            print(f"wrote {args.out} ({args.method}, profile {args.profile_name!r})")
        elif args.cmd == "inspect-ipa":
            print(json.dumps(inspect_ipa(Path(args.ipa)), indent=2))
        elif args.cmd == "receipt":
            receipt = build_receipt(
                ipa=Path(args.ipa), profile=json.loads(Path(args.profile_json).read_text()), embedded=json.loads(Path(args.embedded_profile_json).read_text()),
                codesign_text=Path(args.codesign_text).read_text(errors="replace"), entitlements=load_plist(Path(args.entitlements_plist).read_bytes()),
                commit=args.commit, method=args.method, build_number=args.build_number)
            Path(args.out).write_text(json.dumps(receipt, indent=2, default=str) + "\n")
            print(f"receipt OK: {receipt['package']['bundle_id']} {receipt['package']['version']} ({receipt['package']['build']}) sha256 {receipt['package']['sha256']} for {receipt['installs_on']}")
        elif args.cmd == "package":
            result = package(Path(args.ipa), args.base_url, Path(args.out), title=args.title)
            print(json.dumps(result, indent=2))
    except Rejected as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
