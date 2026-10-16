"""scripts/direct_install.py on real plist and zip inputs: profile validation (decoded plists only),
export method selection, export options, package inspection, receipt cross-checks, manifest/page."""

import datetime as dt
import hashlib
import json
import importlib.util
import io
import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "direct_install.py"
spec = importlib.util.spec_from_file_location("direct_install", SCRIPT)
di = importlib.util.module_from_spec(spec)
spec.loader.exec_module(di)

UDID = "00008101-000A1B2C3D4E5F67"
NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=dt.timezone.utc)


def adhoc_profile(**over):
    base = {
        "Name": "rlm-adk-ios adhoc", "UUID": "11111111-2222-3333-4444-555555555555", "Platform": ["iOS"],
        "TeamIdentifier": [di.TEAM], "ProvisionedDevices": [UDID],
        # plistlib (and a decoded real profile) carry naive UTC datetimes; the validator treats naive as UTC.
        "ExpirationDate": dt.datetime(2027, 10, 13, 0, 0),
        "DeveloperCertificates": [b"\x30\x82cert-der-bytes"],
        "Entitlements": {"application-identifier": f"{di.TEAM}.{di.BUNDLE}", "get-task-allow": False, "com.apple.developer.team-identifier": di.TEAM},
    }
    base.update(over)
    return base


def make_ipa(path: Path, info: dict, *, app="RLMADK.app", binary=True, extra_apps=()):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(f"Payload/{app}/Info.plist", plistlib.dumps(info, fmt=plistlib.FMT_BINARY if binary else plistlib.FMT_XML))
        z.writestr(f"Payload/{app}/RLMADK", b"\xcf\xfa\xed\xfe binary")
        z.writestr(f"Payload/{app}/embedded.mobileprovision", b"\x30\x82 cms bytes (not decoded here)")
        for other in extra_apps:
            z.writestr(f"Payload/{other}/Info.plist", plistlib.dumps(info))
    return path


INFO = {"CFBundleIdentifier": di.BUNDLE, "CFBundleShortVersionString": "0.2.0", "CFBundleVersion": "7", "CFBundleDisplayName": "RLM-ADK & <Voice> \"Portal\"", "MinimumOSVersion": "17.0"}
CODESIGN = """Executable=/x/Payload/RLMADK.app/RLMADK
Identifier=com.rawleystanhope.rlmadk
Format=app bundle with Mach-O thin (arm64)
CodeDirectory v=20400 size=1234 flags=0x0(none) hashes=30+7 location=embedded
CDHash=abcdef0123456789abcdef0123456789abcdef01
Signature size=4800
Authority=Apple Distribution: Rawley Stanhope (4PJBKY2WQR)
Authority=Apple Worldwide Developer Relations Certification Authority
Authority=Apple Root CA
TeamIdentifier=4PJBKY2WQR
Sealed Resources version=2 rules=10 files=12
"""
ENTITLEMENTS = {"application-identifier": f"{di.TEAM}.{di.BUNDLE}", "com.apple.developer.team-identifier": di.TEAM, "keychain-access-groups": [f"{di.TEAM}.{di.BUNDLE}"]}


class ProfileValidationTests(unittest.TestCase):
    def test_adhoc_profile_with_the_device_is_accepted_with_identity_and_expiry(self):
        summary = di.validate_profile(adhoc_profile(), udid=UDID.lower(), now=NOW)
        self.assertEqual((summary["name"], summary["uuid"], summary["team"]), ("rlm-adk-ios adhoc", "11111111-2222-3333-4444-555555555555", di.TEAM))
        self.assertEqual(summary["devices"], [UDID])
        self.assertEqual(summary["expires_at"], "2027-10-13T00:00:00+00:00")
        self.assertEqual(summary["days_left"], 364.5)
        self.assertFalse(summary["near_expiry"])
        self.assertEqual(summary["certificate_sha256"], [hashlib.sha256(b"\x30\x82cert-der-bytes").hexdigest()])
        self.assertFalse(summary["get_task_allow"])

    def test_real_plist_round_trip_xml_and_binary(self):
        for fmt in (plistlib.FMT_XML, plistlib.FMT_BINARY):
            data = plistlib.dumps(adhoc_profile(), fmt=fmt)
            self.assertEqual(di.validate_profile(di.load_plist(data), udid=UDID, now=NOW)["uuid"], "11111111-2222-3333-4444-555555555555")

    def test_malformed_plists_are_rejected_not_parsed_loosely(self):
        for bad in (b"", b"\x30\x82\x05\x00 not a plist", b"<?xml version='1.0'?><plist version='1.0'><dict><key>Name</key>", plistlib.dumps(["not", "a", "dict"])):
            with self.assertRaises(di.Rejected):
                di.validate_profile(di.load_plist(bad), udid=UDID, now=NOW)

    def test_rejections_carry_their_reason(self):
        cases = [
            (adhoc_profile(ProvisionedDevices=["00008101-FFFFFFFFFFFFFFFF"]), "is not in ProvisionedDevices"),
            (adhoc_profile(ProvisionedDevices=None), "App Store profile"),
            ({k: v for k, v in adhoc_profile().items() if k != "ProvisionedDevices"}, "App Store profile"),
            (adhoc_profile(ProvisionsAllDevices=True), "enterprise"),
            (adhoc_profile(Entitlements={"application-identifier": f"{di.TEAM}.{di.BUNDLE}", "get-task-allow": True}), "development (debugging) profile"),
            (adhoc_profile(Entitlements={"application-identifier": f"{di.TEAM}.{di.BUNDLE}", "beta-reports-active": True}), "beta-reports-active"),
            (adhoc_profile(ExpirationDate=dt.datetime(2026, 10, 12)), "expired"),
            (adhoc_profile(ExpirationDate=dt.datetime(2026, 10, 12, tzinfo=dt.timezone.utc)), "expired"),
            (adhoc_profile(TeamIdentifier=["OTHERTEAM1"]), "does not include 4PJBKY2WQR"),
            (adhoc_profile(Entitlements={"application-identifier": f"{di.TEAM}.com.other.app"}), "application-identifier"),
            ({k: v for k, v in adhoc_profile().items() if k != "ExpirationDate"}, "ExpirationDate missing"),
            ({k: v for k, v in adhoc_profile().items() if k != "UUID"}, "Name/UUID"),
        ]
        for plist, reason in cases:
            with self.assertRaises(di.Rejected) as ctx:
                di.validate_profile(plist, udid=UDID, now=NOW)
            self.assertIn(reason, str(ctx.exception), reason)
        with self.assertRaises(di.Rejected) as ctx:
            di.validate_profile(adhoc_profile(), udid="", now=NOW)
        self.assertIn("no target device UDID", str(ctx.exception))

    def test_near_expiry_is_informational_not_a_rejection(self):
        summary = di.validate_profile(adhoc_profile(ExpirationDate=(NOW + dt.timedelta(days=10)).replace(tzinfo=None)), udid=UDID, now=NOW)
        self.assertTrue(summary["near_expiry"])
        self.assertEqual(summary["days_left"], 10.0)


class ExportTests(unittest.TestCase):
    def test_method_selection_prefers_release_testing_and_falls_back_to_ad_hoc(self):
        self.assertEqual(di.select_method("method values: app-store-connect, release-testing, enterprise, debugging, developer-id"), "release-testing")
        self.assertEqual(di.select_method("Available methods: app-store, ad-hoc, enterprise, development"), "ad-hoc")
        with self.assertRaises(di.Rejected):
            di.select_method("methods: app-store-connect, enterprise, debugging")
        with self.assertRaises(di.Rejected):
            di.select_method("ad-hoc-like words such as pre-release-testing-builds do not count")

    def test_export_options_plist_is_manual_distribution_for_the_profile(self):
        opts = plistlib.loads(di.export_options("release-testing", "rlm-adk-ios adhoc"))
        self.assertEqual(opts, {"method": "release-testing", "destination": "export", "teamID": di.TEAM, "signingStyle": "manual", "signingCertificate": "Apple Distribution", "provisioningProfiles": {di.BUNDLE: "rlm-adk-ios adhoc"}, "thinning": "<none>"})
        for forbidden in ("uploadSymbols", "testFlightInternalTestingOnly", "manageAppVersionAndBuildNumber"):
            self.assertNotIn(forbidden, opts)
        with self.assertRaises(di.Rejected):
            di.export_options("app-store-connect", "x")


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ipa = make_ipa(self.dir / "RLMADK.ipa", INFO)

    def tearDown(self):
        self.tmp.cleanup()

    def test_identity_comes_from_the_package_binary_or_xml_plist(self):
        for binary in (True, False):
            ipa = make_ipa(self.dir / f"b{binary}.ipa", INFO, binary=binary)
            identity = di.inspect_ipa(ipa)
            self.assertEqual((identity["bundle_id"], identity["version"], identity["build"], identity["app"]), (di.BUNDLE, "0.2.0", "7", "RLMADK.app"))
            self.assertEqual(identity["sha256"], hashlib.sha256(ipa.read_bytes()).hexdigest())
            self.assertEqual(identity["size_bytes"], ipa.stat().st_size)

    def test_bad_packages_are_rejected(self):
        with zipfile.ZipFile(self.dir / "nopayload.ipa", "w") as z:
            z.writestr("README", "x")
        with self.assertRaises(di.Rejected):
            di.inspect_ipa(self.dir / "nopayload.ipa")
        with zipfile.ZipFile(self.dir / "noinfo.ipa", "w") as z:
            z.writestr("Payload/RLMADK.app/RLMADK", "x")
        with self.assertRaises(di.Rejected):
            di.inspect_ipa(self.dir / "noinfo.ipa")
        with self.assertRaises(di.Rejected):
            di.inspect_ipa(make_ipa(self.dir / "two.ipa", INFO, extra_apps=("Other.app",)))

    def test_manifest_and_page_use_package_identity_with_escaping(self):
        result = di.package(self.ipa, "https://portal.example.ts.net/ios/build7", self.dir / "out")
        manifest = plistlib.loads((self.dir / "out" / "manifest.plist").read_bytes())
        item = manifest["items"][0]
        self.assertEqual(item["assets"], [{"kind": "software-package", "url": "https://portal.example.ts.net/ios/build7/RLMADK.ipa"}])
        self.assertEqual(item["metadata"], {"bundle-identifier": di.BUNDLE, "bundle-version": "7", "kind": "software", "title": 'RLM-ADK & <Voice> "Portal"'})
        page = (self.dir / "out" / "index.html").read_text()
        self.assertIn("RLM-ADK &amp; &lt;Voice&gt; &quot;Portal&quot;", page)
        self.assertNotIn('<Voice> "Portal"', page)
        self.assertIn('href="itms-services://?action=download-manifest&amp;url=https%3A%2F%2Fportal.example.ts.net%2Fios%2Fbuild7%2Fmanifest.plist"', page)
        self.assertEqual(result["install_link"], "itms-services://?action=download-manifest&url=https%3A%2F%2Fportal.example.ts.net%2Fios%2Fbuild7%2Fmanifest.plist")
        sidecar = (self.dir / "out" / "RLMADK.ipa.sha256").read_text()
        self.assertEqual(sidecar, f"{hashlib.sha256(self.ipa.read_bytes()).hexdigest()}  RLMADK.ipa\n")
        self.assertIn(result["identity"]["sha256"], page)
        self.assertIn("Developer Mode", page)

    def test_publish_places_the_build_then_points_latest_json_at_it(self):
        root = self.dir / "delivery"
        doc = di.publish(self.ipa, root, "https://portal.example.ts.net:8443/", notes="Adds the update banner.")
        self.assertEqual(doc["schema"], "rlmadk.latest_build.v1")
        self.assertEqual(doc["build"], 7)
        self.assertEqual(doc["bundle_id"], di.BUNDLE)
        self.assertEqual(doc["page_url"], "https://portal.example.ts.net:8443/build7/")
        self.assertEqual(doc["install_url"], "itms-services://?action=download-manifest&url=https%3A%2F%2Fportal.example.ts.net%3A8443%2Fbuild7%2Fmanifest.plist")
        self.assertEqual(doc["notes"], "Adds the update banner.")
        self.assertEqual(json.loads((root / "latest.json").read_text()), doc)
        for name in ("RLMADK.ipa", "RLMADK.ipa.sha256", "manifest.plist", "index.html", "latest.json"):
            self.assertTrue((root / "build7" / name).is_file(), name)
        self.assertIn('href="build7/"', (root / "index.html").read_text())
        self.assertFalse((root / ".latest.json.tmp").exists())
        again = di.publish(root / "build7" / "RLMADK.ipa", root, "https://portal.example.ts.net:8443")
        self.assertEqual(again["build"], 7, "republishing the same bytes is allowed")

    def test_publish_refuses_a_reused_build_number_with_different_bytes(self):
        root = self.dir / "delivery"
        di.publish(self.ipa, root, "https://portal.example.ts.net:8443")
        (self.dir / "other").mkdir()
        other = make_ipa(self.dir / "other" / "RLMADK.ipa", {**INFO, "Different": "bytes"})
        with self.assertRaises(di.Rejected):
            di.publish(other, root, "https://portal.example.ts.net:8443")

    def test_only_https_base_urls_are_accepted(self):
        for bad in ("http://portal.example/ios", "portal.example/ios", "https:///nohost", "ftp://x/y"):
            with self.assertRaises(di.Rejected):
                di.package(self.ipa, bad, self.dir / "o")
        self.assertFalse((self.dir / "o").exists())


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ipa = make_ipa(self.dir / "RLMADK.ipa", INFO)
        self.profile = di.validate_profile(adhoc_profile(), udid=UDID, now=NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def receipt(self, **over):
        kw = dict(ipa=self.ipa, profile=self.profile, embedded=dict(self.profile), codesign_text=CODESIGN, entitlements=dict(ENTITLEMENTS), commit="f388601", method="release-testing", build_number="7")
        kw.update(over)
        return di.build_receipt(**kw)

    def test_receipt_ties_package_signing_profile_and_commit(self):
        r = self.receipt()
        self.assertEqual((r["commit"], r["export_method"], r["package"]["build"], r["package"]["bundle_id"]), ("f388601", "release-testing", "7", di.BUNDLE))
        self.assertEqual(r["signing"]["team"], di.TEAM)
        self.assertEqual(r["signing"]["authorities"][0], "Apple Distribution: Rawley Stanhope (4PJBKY2WQR)")
        self.assertEqual(r["signing"]["cdhash"], "abcdef0123456789abcdef0123456789abcdef01")
        self.assertEqual(r["installs_on"], [UDID])
        self.assertEqual(r["expires_at"], "2027-10-13T00:00:00+00:00")
        self.assertEqual(r["package"]["sha256"], hashlib.sha256(self.ipa.read_bytes()).hexdigest())
        self.assertNotIn("get-task-allow", r["signing"]["entitlements"])

    def test_receipt_is_refused_on_any_mismatch(self):
        cases = {
            "wrong build": dict(build_number="8"),
            "embedded profile differs": dict(embedded={**self.profile, "uuid": "other"}),
            "embedded devices differ": dict(embedded={**self.profile, "devices": ["00008101-0000000000000000"]}),
            "development signing": dict(codesign_text=CODESIGN.replace("Authority=Apple Distribution: Rawley Stanhope (4PJBKY2WQR)", "Authority=Apple Development: Rawley Stanhope (ABC123)")),
            "other team": dict(codesign_text=CODESIGN.replace("TeamIdentifier=4PJBKY2WQR", "TeamIdentifier=OTHERTEAM1")),
            "other identifier": dict(codesign_text=CODESIGN.replace("Identifier=com.rawleystanhope.rlmadk", "Identifier=com.other.app")),
            "debuggable": dict(entitlements={**ENTITLEMENTS, "get-task-allow": True}),
            "testflight": dict(entitlements={**ENTITLEMENTS, "beta-reports-active": True}),
            "entitlement app id": dict(entitlements={**ENTITLEMENTS, "application-identifier": "OTHERTEAM1.com.rawleystanhope.rlmadk"}),
        }
        for name, over in cases.items():
            with self.assertRaises(di.Rejected, msg=name):
                self.receipt(**over)

    def test_cli_round_trip_writes_receipt_and_package(self):
        pj = self.dir / "profile.json"; pj.write_text(__import__("json").dumps(self.profile))
        cs = self.dir / "codesign.txt"; cs.write_text(CODESIGN)
        ent = self.dir / "ent.plist"; ent.write_bytes(plistlib.dumps(ENTITLEMENTS))
        out = self.dir / "receipt.json"
        rc = di.main(["receipt", "--ipa", str(self.ipa), "--profile-json", str(pj), "--embedded-profile-json", str(pj), "--codesign-text", str(cs), "--entitlements-plist", str(ent), "--commit", "f388601", "--method", "release-testing", "--build-number", "7", "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertEqual(__import__("json").loads(out.read_text())["package"]["build"], "7")
        self.assertEqual(di.main(["receipt", "--ipa", str(self.ipa), "--profile-json", str(pj), "--embedded-profile-json", str(pj), "--codesign-text", str(cs), "--entitlements-plist", str(ent), "--commit", "f388601", "--method", "release-testing", "--build-number", "99", "--out", str(self.dir / "no.json")]), 1)
        self.assertFalse((self.dir / "no.json").exists())
        pl = self.dir / "profile.plist"; pl.write_bytes(plistlib.dumps(adhoc_profile()))
        self.assertEqual(di.main(["validate-profile", "--plist", str(pl), "--udid", UDID, "--json", str(self.dir / "v.json")]), 0)
        self.assertEqual(di.main(["validate-profile", "--plist", str(pl), "--udid", "00008101-DEADBEEFDEADBEEF", "--json", str(self.dir / "v2.json")]), 1)
        self.assertFalse((self.dir / "v2.json").exists())
        self.assertEqual(di.main(["package", "--ipa", str(self.ipa), "--base-url", "https://h.example/p", "--out", str(self.dir / "pk")]), 0)
        self.assertTrue((self.dir / "pk" / "manifest.plist").exists())


if __name__ == "__main__":
    unittest.main()
