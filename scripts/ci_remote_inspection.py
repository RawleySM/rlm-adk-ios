#!/usr/bin/env python3
"""CI exercise of phone inspection against the fixture backend and the real simulator client.

Waits for the app to register, then issues snapshot / image / select_tab / sync_manifest /
copy_capture_files commands and polls each until the phone reports applied or failed. Writes a JSON
report and the latest app-rendered image. Exits non-zero when a command the simulator can perform
does not come back `applied`, or when the copied bytes do not match the seeded file. Stdlib only.
"""

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request


def call(base, method, path, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=body, headers={"Content-Type": "application/json"} if body else {}, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
        return json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw


def wait_for(fn, timeout, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.5)
    raise SystemExit(f"timeout waiting for {what}")


def run_command(base, install_id, kind, args=None, timeout=30):
    created = call(base, "POST", f"/devices/{install_id}/commands", {"kind": kind, "args": args or {}})
    final = wait_for(lambda: (lambda c: c if c["status"] in ("applied", "failed", "unsupported") else None)(call(base, "GET", f"/devices/{install_id}/commands/{created['command_id']}")), timeout, f"{kind} result")
    print(f"{kind}: created {created['status']} pushed={created['pushed']} -> {final['status']} {final.get('result')}")
    return final


def main():
    base, seed_path, seed_capture, out_dir = sys.argv[1:5]
    install_id = wait_for(lambda: next((d["install_id"] for d in call(base, "GET", "/devices")["devices"] if d.get("connected")), None), 60, "a connected device")
    report = {"install_id": install_id, "backend": base, "commands": {}}
    report["commands"]["snapshot"] = run_command(base, install_id, "snapshot")
    snap = call(base, "GET", f"/devices/{install_id}/snapshot")
    report["snapshot"] = snap
    print(f"snapshot freshness={snap['freshness']} age={snap['age_s']} tab={snap['snapshot']['selected_tab']} keyboard={snap['snapshot']['keyboard_visible']} owner={snap['snapshot']['audio_owner_state']}")
    # Tab transition: portal first, then another tab; the snapshot the app sends on each change must
    # name the new tab (observed state, not the command's intent).
    report["commands"]["select_tab_portal"] = run_command(base, install_id, "select_tab", {"tab": "portal"})
    after_portal = wait_for(lambda: (lambda s: s if s.get("snapshot", {}).get("selected_tab") == "portal" else None)(call(base, "GET", f"/devices/{install_id}/snapshot")), 15, "snapshot showing portal")
    report["commands"]["select_tab_work"] = run_command(base, install_id, "select_tab", {"tab": "work"})
    after_work = wait_for(lambda: (lambda s: s if s.get("snapshot", {}).get("selected_tab") == "work" else None)(call(base, "GET", f"/devices/{install_id}/snapshot")), 15, "snapshot showing work")
    report["tab_transition"] = {"portal": {"reason": after_portal["reason"], "client_time": after_portal["client_time"]}, "work": {"reason": after_work["reason"], "client_time": after_work["client_time"]}}
    print(f"tab transition observed: portal ({after_portal['reason']}) -> work ({after_work['reason']})")
    report["commands"]["select_tab_unknown"] = run_command(base, install_id, "select_tab", {"tab": "nowhere"})
    report["commands"]["dismiss_keyboard"] = run_command(base, install_id, "dismiss_keyboard")
    report["commands"]["image"] = run_command(base, install_id, "image")
    meta = call(base, "GET", f"/devices/{install_id}/images/latest/meta")
    image = call(base, "GET", f"/devices/{install_id}/images/latest")
    with open(f"{out_dir}/simulator-remote-image.jpg", "wb") as f:
        f.write(image)
    report["image"] = {"bytes": len(image), "sha256": hashlib.sha256(image).hexdigest(), "meta_sha256": meta["sha256"], "meta_command_id": meta["command_id"], "command_id": report["commands"]["image"]["command_id"], "phone_reported_sha256": report["commands"]["image"].get("result", {}).get("sha256"), "age_s": meta["age_s"], "scope": meta["scope"], "snapshot_tab": (meta.get("snapshot") or {}).get("selected_tab")}
    report["commands"]["sync_manifest"] = run_command(base, install_id, "sync_manifest")
    manifest = call(base, "GET", f"/devices/{install_id}/manifest")["manifest"]
    report["manifest"] = {"captures": [(c["capture_id"], c["kind"], len(c["segments"])) for c in manifest["captures"]], "orphans": manifest["orphans"]}
    report["commands"]["copy_unknown"] = run_command(base, install_id, "copy_capture_files", {"capture_id": "prayer-does-not-exist"})
    report["commands"]["copy_capture_files"] = run_command(base, install_id, "copy_capture_files", {"capture_id": seed_capture}, timeout=60)
    evidence = call(base, "GET", f"/devices/{install_id}/captures/{seed_capture}")
    with open(seed_path, "rb") as f:
        seed = f.read()
    seed_sha = hashlib.sha256(seed).hexdigest()
    copied = [(f["segment_index"], f["bytes"], f["sha256"], f["path"]) for f in evidence["files"]]
    report["copy"] = {"seed_sha256": seed_sha, "seed_bytes": len(seed), "files": copied}
    with open(f"{out_dir}/remote-inspection.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    problems = []
    for name in ("snapshot", "select_tab_portal", "select_tab_work", "dismiss_keyboard", "image", "sync_manifest", "copy_capture_files"):
        if report["commands"][name]["status"] != "applied":
            problems.append(f"{name} not applied: {report['commands'][name]}")
    for name in ("select_tab_unknown", "copy_unknown"):
        if report["commands"][name]["status"] != "failed":
            problems.append(f"{name} should have failed: {report['commands'][name]}")
    if report["image"]["sha256"] != meta["sha256"] or len(image) < 1000:
        problems.append("image digest/size mismatch")
    if meta["command_id"] != report["commands"]["image"]["command_id"] or report["image"]["phone_reported_sha256"] != meta["sha256"]:
        problems.append(f"image is not the one the phone reported for this command: {report['image']}")
    if report["image"]["snapshot_tab"] != "work":
        problems.append(f"image snapshot does not show the current tab (work): {report['image']['snapshot_tab']}")
    # This endpoint stores the latest observation. A heartbeat can replace the tab-change
    # snapshot before our GET, while still proving the selected tab after the applied command.
    # The waits above require portal then work; their client timestamps must also advance.
    if after_work["client_time"] <= after_portal["client_time"]:
        problems.append(f"tab transition snapshots not in order: {report['tab_transition']}")
    if report["commands"]["dismiss_keyboard"].get("result", {}).get("keyboard_visible_after") != "no":
        problems.append(f"dismiss did not report the observed post-state: {report['commands']['dismiss_keyboard']}")
    if not any(c[1] == len(seed) and c[2] == seed_sha for c in copied):
        problems.append(f"seeded file not copied byte-for-byte: {copied}")
    else:
        path = next(c[3] for c in copied if c[2] == seed_sha)
        with open(path, "rb") as f:
            if f.read() != seed:
                problems.append("stored bytes differ from seed")
    if snap["freshness"] != "live":
        problems.append(f"snapshot freshness {snap['freshness']}")
    if problems:
        print("REMOTE INSPECTION PROBLEMS:\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("remote inspection exercised: all commands reported by the simulator client")


if __name__ == "__main__":
    main()
