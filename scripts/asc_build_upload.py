#!/usr/bin/env python3
"""Upload a signed IPA to App Store Connect through the Build Upload API (stdlib + openssl only).

Contract (App Store Connect API 4.5.1 OpenAPI, resources buildUploads / buildUploadFiles):
  1. POST  /v1/buildUploads           BuildUploadCreateRequest {cfBundleShortVersionString, cfBundleVersion, platform} + app
  2. POST  /v1/buildUploadFiles       BuildUploadFileCreateRequest {fileName, fileSize, uti=com.apple.ipa, assetType=ASSET} + buildUpload
                                      -> attributes.uploadOperations[{method,url,offset,length,requestHeaders,partNumber}]
  3. PUT   each operation's byte range with its request headers
  4. PATCH /v1/buildUploadFiles/{id}  BuildUploadFileUpdateRequest {uploaded: true, sourceFileChecksums: {file: {hash, algorithm: MD5}}}
  5. GET   /v1/buildUploads/{id}      state.state in AWAITING_UPLOAD | PROCESSING | FAILED | COMPLETE; then /v1/builds for the app

Every identifier is written to the receipt BEFORE the next side effect, so a failed invocation can be
re-run with --receipt (or --build-upload-id) and reconcile the existing upload/file instead of
creating a second one. A build number that already has a build on the app is never re-sent.
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

DEFAULT_API = "https://api.appstoreconnect.apple.com"


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
    """JSON client; `token` may be injected (tests) instead of a key file."""

    def __init__(self, key_path: str | None, key_id: str, issuer: str, api: str = DEFAULT_API, token: str | None = None):
        self.key_path, self.key_id, self.issuer, self.api, self.token = key_path, key_id, issuer, api, token

    def _auth(self) -> str:
        return self.token or make_jwt(self.key_path, self.key_id, self.issuer)

    def call(self, method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(self.api + path, data=json.dumps(payload).encode() if payload else None, method=method,
                                     headers={"Authorization": f"Bearer {self._auth()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")


class Receipt:
    """Append-only JSON receipt persisted after every step (ids survive a crash)."""

    def __init__(self, path: str | None):
        self.path = path
        self.data: dict = {"steps": []}
        if path and os.path.isfile(path):
            with open(path) as f:
                self.data = json.load(f)
            self.data.setdefault("steps", [])

    def set(self, **fields) -> None:
        self.data.update(fields)
        self.save()

    def step(self, name: str, **fields) -> None:
        self.data["steps"].append({"step": name, "at": time.time(), **fields})
        self.save()

    def save(self) -> None:
        if self.path:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.data, f, indent=1, default=str)
            os.replace(tmp, self.path)


def ipa_versions(path: str) -> tuple[str, str]:
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.count("/") == 2 and n.endswith(".app/Info.plist"))
        info = plistlib.loads(z.read(name))
    return info["CFBundleShortVersionString"], info["CFBundleVersion"]


def commit_payload(file_id: str, md5_hex: str) -> dict:
    return {"data": {"type": "buildUploadFiles", "id": file_id, "attributes": {"uploaded": True, "sourceFileChecksums": {"file": {"hash": md5_hex, "algorithm": "MD5"}}}}}


def reserve_payload(upload_id: str, file_name: str, size: int) -> dict:
    return {"data": {"type": "buildUploadFiles", "attributes": {"fileName": file_name, "fileSize": size, "uti": "com.apple.ipa", "assetType": "ASSET"}, "relationships": {"buildUpload": {"data": {"type": "buildUploads", "id": upload_id}}}}}


def create_payload(app_id: str, version: str, build: str) -> dict:
    return {"data": {"type": "buildUploads", "attributes": {"cfBundleShortVersionString": version, "cfBundleVersion": build, "platform": "IOS"}, "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}}}


def put_parts(ops: list[dict], data: bytes, receipt: Receipt, log) -> None:
    done = {s.get("part") for s in receipt.data["steps"] if s.get("step") == "part" and s.get("http") in (200, 201, 204)}
    for op in sorted(ops, key=lambda o: o.get("partNumber", o["offset"])):
        n = op.get("partNumber") or (op["offset"] // max(op["length"], 1) + 1)
        if n in done:
            log(f"  part {n}: already uploaded (receipt)")
            continue
        chunk = data[op["offset"]:op["offset"] + op["length"]]
        req = urllib.request.Request(op["url"], data=chunk, method=op.get("method", "PUT"))
        for h in op.get("requestHeaders") or []:
            req.add_header(h["name"], h["value"])
        with urllib.request.urlopen(req, timeout=600) as r:
            log(f"  part {n}/{len(ops)}: {len(chunk)} bytes -> HTTP {r.status}")
            receipt.step("part", part=n, bytes=len(chunk), http=r.status)


def run(args, client: Client, log=print) -> int:
    version, build = ipa_versions(args.ipa)
    if (args.version and args.version != version) or (args.build and args.build != build):
        log(f"IPA is {version} ({build}) but {args.version} ({args.build}) was requested")
        return 2
    receipt = Receipt(args.receipt)
    receipt.set(ipa=os.path.abspath(args.ipa), version=version, build=build, app_id=args.app_id)

    st, existing = client.call("GET", f"/v1/builds?filter[app]={args.app_id}&filter[version]={build}&limit=5")
    for b in existing.get("data", []):
        if b["attributes"].get("version") == build:
            log(f"build {build} already exists for app {args.app_id} (id {b['id']}, {b['attributes'].get('processingState')}); not re-sending")
            receipt.set(existing_build=b)
            return 3

    upload_id = args.build_upload_id or receipt.data.get("build_upload_id")
    if upload_id:
        st, d = client.call("GET", f"/v1/buildUploads/{upload_id}")
        if st != 200:
            log(f"build upload {upload_id} not readable ({st}); refusing to guess")
            receipt.step("reconcile_upload", http=st, errors=d.get("errors"))
            return 1
        attrs = d["data"]["attributes"]
        if (attrs.get("cfBundleShortVersionString"), attrs.get("cfBundleVersion")) != (version, build):
            log(f"build upload {upload_id} is for {attrs.get('cfBundleShortVersionString')} ({attrs.get('cfBundleVersion')}), not this IPA")
            return 1
        receipt.set(build_upload_id=upload_id)
        receipt.step("reconcile_upload", http=st, state=attrs.get("state"))
        if (attrs.get("state") or {}).get("state") in ("COMPLETE", "PROCESSING"):
            log(f"build upload {upload_id} already {attrs['state']['state']}; nothing to send")
            return 0
    else:
        st, d = client.call("POST", "/v1/buildUploads", create_payload(args.app_id, version, build))
        receipt.step("create_upload", http=st, errors=d.get("errors"))
        if st != 201:
            log(f"buildUploads failed {st} {json.dumps(d)[:600]}")
            return 1
        upload_id = d["data"]["id"]
        receipt.set(build_upload_id=upload_id)
    log(f"build upload {upload_id}")

    with open(args.ipa, "rb") as f:
        data = f.read()
    size = len(data)
    file_id = receipt.data.get("build_upload_file_id")
    ops: list[dict] = []
    if file_id:
        st, d = client.call("GET", f"/v1/buildUploadFiles/{file_id}?fields[buildUploadFiles]=assetDeliveryState,fileName,fileSize,uploadOperations")
        receipt.step("reconcile_file", http=st, state=(d.get("data", {}).get("attributes", {}) or {}).get("assetDeliveryState"))
        if st == 200:
            a = d["data"]["attributes"]
            state = (a.get("assetDeliveryState") or {}).get("state")
            if state in ("UPLOAD_COMPLETE", "COMPLETE"):
                log(f"file {file_id} already {state}; skipping upload and commit")
                file_id = None
                ops = []
                committed = True
            elif a.get("fileName") == os.path.basename(args.ipa) and int(a.get("fileSize", -1)) == size:
                ops = a.get("uploadOperations") or []
                committed = False
                log(f"reusing file reservation {file_id} ({state}); {len(ops)} operation(s)")
            else:
                log(f"file {file_id} does not match this IPA; a fresh reservation is needed")
                file_id = None
                committed = False
        else:
            file_id = None
            committed = False
    else:
        committed = False
    if not file_id and not committed:
        st, d = client.call("POST", "/v1/buildUploadFiles", reserve_payload(upload_id, os.path.basename(args.ipa), size))
        receipt.step("reserve_file", http=st, errors=d.get("errors"))
        if st != 201:
            log(f"buildUploadFiles failed {st} {json.dumps(d)[:600]}")
            return 1
        file_id = d["data"]["id"]
        ops = d["data"]["attributes"].get("uploadOperations") or []
        receipt.set(build_upload_file_id=file_id, upload_operations=len(ops))
        log(f"file reservation {file_id}: {len(ops)} upload operation(s)")
    if file_id and not committed:
        put_parts(ops, data, receipt, log)
        md5_hex = hashlib.md5(data).hexdigest()
        st, d = client.call("PATCH", f"/v1/buildUploadFiles/{file_id}", commit_payload(file_id, md5_hex))
        receipt.step("commit_file", http=st, md5=md5_hex, state=(d.get("data", {}).get("attributes", {}) or {}).get("assetDeliveryState"), errors=d.get("errors"))
        if st != 200:
            log(f"commit failed {st} {json.dumps(d)[:600]}")
            return 1
        log("committed; asset delivery: " + json.dumps(d["data"]["attributes"].get("assetDeliveryState")))

    deadline = time.time() + args.poll_seconds
    state: dict = {}
    while True:
        st, d = client.call("GET", f"/v1/buildUploads/{upload_id}")
        state = (d.get("data", {}).get("attributes", {}) or {}).get("state") or {}
        log("upload state: " + json.dumps(state))
        if state.get("state") in ("COMPLETE", "FAILED") or time.time() >= deadline:
            break
        time.sleep(args.poll_interval)
    receipt.set(final_state=state)
    st, d = client.call("GET", f"/v1/builds?filter[app]={args.app_id}&filter[version]={build}&limit=5")
    builds = [{"id": b["id"], **{k: b["attributes"].get(k) for k in ("version", "processingState", "uploadedDate", "expired")}} for b in d.get("data", [])]
    receipt.set(builds=builds)
    log("builds for this number: " + json.dumps(builds))
    return 0 if state.get("state") == "COMPLETE" else 1


def parse(argv=None):
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
    ap.add_argument("--poll-interval", type=int, default=30)
    ap.add_argument("--receipt", help="JSON receipt (read for reconciliation, written after every step)")
    ap.add_argument("--api", default=DEFAULT_API)
    return ap.parse_args(argv)


def main() -> int:
    args = parse()
    return run(args, Client(args.key_path, args.key_id, args.issuer_id, api=args.api))


if __name__ == "__main__":
    sys.exit(main())
