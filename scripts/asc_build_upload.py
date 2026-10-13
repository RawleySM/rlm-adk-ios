#!/usr/bin/env python3
"""Upload a signed IPA to App Store Connect through the Build Upload API (stdlib + openssl only).

Flow (App Store Connect API, buildUploads/buildUploadFiles):
  1. POST /v1/buildUploads            {cfBundleShortVersionString, cfBundleVersion, platform, app}
  2. POST /v1/buildUploadFiles        {fileName, fileSize, assetType=ASSET, uti=com.apple.ipa, buildUpload}
     -> uploadOperations (PUT chunks with the given headers)
  3. PATCH /v1/buildUploadFiles/{id}  {uploaded: true, sourceFileChecksum: md5}
  4. poll GET /v1/buildUploads/{id}   until state COMPLETE/FAILED, then the build appears under the app.

Usage: asc_build_upload.py --ipa PATH --app-id ID --key-id KID --issuer-id ISS --key-path AuthKey.p8
       [--version 0.2.0 --build 3] [--build-upload-id EXISTING] [--poll-seconds 600]
Never re-sends a build number that already has a build: it checks the app's builds first.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

API = "https://api.appstoreconnect.apple.com"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def der_to_raw(der: bytes) -> bytes:
    i = 2 if der[1] < 0x80 else 2 + (der[1] & 0x7F)
    out = b""
    for _ in range(2):
        ln = der[i + 1]
        out += der[i + 2:i + 2 + ln].lstrip(b"\x00").rjust(32, b"\x00")
        i += 2 + ln
    return out


def make_jwt(key_path: str, key_id: str, issuer: str) -> str:
    now = int(time.time())
    head = b64url(json.dumps({"alg": "ES256", "kid": key_id, "typ": "JWT"}).encode())
    body = b64url(json.dumps({"iss": issuer, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"}).encode())
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(f"{head}.{body}".encode())
        tmp = f.name
    try:
        der = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key_path, tmp], capture_output=True, check=True).stdout
    finally:
        os.unlink(tmp)
    return f"{head}.{body}.{b64url(der_to_raw(der))}"


class Client:
    def __init__(self, key_path: str, key_id: str, issuer: str):
        self.key_path, self.key_id, self.issuer = key_path, key_id, issuer

    def call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(API + path, data=json.dumps(payload).encode() if payload else None, method=method,
                                     headers={"Authorization": f"Bearer {make_jwt(self.key_path, self.key_id, self.issuer)}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


def ipa_versions(path: str) -> tuple[str, str]:
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.count("/") == 2 and n.endswith(".app/Info.plist"))
        info = plistlib.loads(z.read(name))
    return info["CFBundleShortVersionString"], info["CFBundleVersion"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ipa", required=True)
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--key-id", required=True)
    ap.add_argument("--issuer-id", required=True)
    ap.add_argument("--key-path", required=True)
    ap.add_argument("--version")
    ap.add_argument("--build")
    ap.add_argument("--build-upload-id")
    ap.add_argument("--poll-seconds", type=int, default=900)
    ap.add_argument("--receipt", help="write a JSON receipt here")
    args = ap.parse_args()
    version, build = ipa_versions(args.ipa)
    if args.version and args.version != version or args.build and args.build != build:
        print(f"IPA is {version} ({build}) but {args.version} ({args.build}) was requested", file=sys.stderr)
        return 2
    c = Client(args.key_path, args.key_id, args.issuer_id)
    receipt: dict = {"ipa": args.ipa, "version": version, "build": build, "app_id": args.app_id, "steps": []}

    st, existing = c.call("GET", f"/v1/builds?filter[app]={args.app_id}&filter[version]={build}&limit=5")
    for b in existing.get("data", []):
        if b["attributes"].get("version") == build:
            print(f"build {build} already exists for app {args.app_id} (id {b['id']}, {b['attributes'].get('processingState')}); not re-sending")
            receipt["existing_build"] = b
            if args.receipt:
                json.dump(receipt, open(args.receipt, "w"), indent=1)
            return 3

    upload_id = args.build_upload_id
    if not upload_id:
        st, d = c.call("POST", "/v1/buildUploads", {"data": {"type": "buildUploads", "attributes": {"cfBundleShortVersionString": version, "cfBundleVersion": build, "platform": "IOS"}, "relationships": {"app": {"data": {"type": "apps", "id": args.app_id}}}}})
        receipt["steps"].append({"buildUploads": st, "response": d})
        if st != 201:
            print("buildUploads failed", st, json.dumps(d)[:800], file=sys.stderr)
            return 1
        upload_id = d["data"]["id"]
    print("build upload", upload_id)

    size = os.path.getsize(args.ipa)
    st, d = c.call("POST", "/v1/buildUploadFiles", {"data": {"type": "buildUploadFiles", "attributes": {"fileName": os.path.basename(args.ipa), "fileSize": size, "assetType": "ASSET", "uti": "com.apple.ipa"}, "relationships": {"buildUpload": {"data": {"type": "buildUploads", "id": upload_id}}}}})
    receipt["steps"].append({"buildUploadFiles": st, "response": {k: v for k, v in d.get("data", {}).get("attributes", {}).items() if k != "uploadOperations"}, "errors": d.get("errors")})
    if st != 201:
        print("buildUploadFiles failed", st, json.dumps(d)[:800], file=sys.stderr)
        return 1
    file_id = d["data"]["id"]
    ops = d["data"]["attributes"].get("uploadOperations") or []
    print(f"file reservation {file_id}: {len(ops)} upload operation(s)")
    data = open(args.ipa, "rb").read()
    for n, op in enumerate(ops, 1):
        chunk = data[op["offset"]:op["offset"] + op["length"]]
        req = urllib.request.Request(op["url"], data=chunk, method=op.get("method", "PUT"))
        for h in op.get("requestHeaders") or []:
            req.add_header(h["name"], h["value"])
        with urllib.request.urlopen(req, timeout=600) as r:
            print(f"  part {n}/{len(ops)}: {len(chunk)} bytes -> HTTP {r.status}")
            receipt["steps"].append({"part": n, "bytes": len(chunk), "http": r.status})
    md5 = hashlib.md5(data).hexdigest()
    st, d = c.call("PATCH", f"/v1/buildUploadFiles/{file_id}", {"data": {"type": "buildUploadFiles", "id": file_id, "attributes": {"uploaded": True, "sourceFileChecksum": md5}}})
    receipt["steps"].append({"commit": st, "md5": md5, "response": d.get("data", {}).get("attributes", {}).get("assetDeliveryState"), "errors": d.get("errors")})
    if st not in (200, 201):
        print("commit failed", st, json.dumps(d)[:800], file=sys.stderr)
        return 1
    print("committed; asset delivery:", json.dumps(d.get("data", {}).get("attributes", {}).get("assetDeliveryState")))

    deadline = time.time() + args.poll_seconds
    state = None
    while time.time() < deadline:
        st, d = c.call("GET", f"/v1/buildUploads/{upload_id}")
        state = d.get("data", {}).get("attributes", {}).get("state", {})
        print("upload state:", json.dumps(state))
        if state.get("state") in ("COMPLETE", "FAILED"):
            break
        time.sleep(30)
    receipt["final_state"] = state
    st, d = c.call("GET", f"/v1/builds?filter[app]={args.app_id}&filter[version]={build}&limit=5")
    receipt["builds"] = [{"id": b["id"], **{k: b["attributes"].get(k) for k in ("version", "processingState", "uploadedDate", "expired")}} for b in d.get("data", [])]
    print("builds for this number:", json.dumps(receipt["builds"]))
    if args.receipt:
        json.dump(receipt, open(args.receipt, "w"), indent=1, default=str)
    return 0 if (state or {}).get("state") == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())
