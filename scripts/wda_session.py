#!/usr/bin/env python3
"""Bounded USB session for preinstalled WebDriverAgent v16.15.0 on iOS 17+.

Requires pymobiledevice3==11.29.0, paired USB, Developer Mode, a mounted developer
image, and the separate signed runner. The main microphone app is never launched
or killed here. First use may show Apple's Enable UI Automation passcode prompt.
"""
from contextlib import AsyncExitStack
import argparse
import asyncio
import json
import logging
import signal
import socket
import urllib.request
import sys
import time


async def activate_existing(process, bundle, expected_pid):
    current = await process.process_identifier_for_bundle_identifier(bundle)
    if not expected_pid or current != expected_pid:
        raise RuntimeError("Microphone process changed before activation; refusing to relaunch")
    actual = await process.launch(bundle, kill_existing=False)
    if actual != expected_pid:
        raise RuntimeError("Activation changed microphone PID; continuity not proved")
    return actual


async def main(args):
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
    from pymobiledevice3.remote.core_device.app_service import AppServiceService
    from pymobiledevice3.services.wda import WdaServiceClient
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.process_control import ProcessControl

    # Detect a conflicting local forward before launching any phone process.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    stopped = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    forward = None
    owned_pid = None
    async with UserspaceRsdTunnel(serial=args.udid, autopair=False) as rsd:
        required = "com.apple.coredevice.appservice"
        if required not in rsd.peer_info.get("Services", {}):
            raise RuntimeError("Developer image not mounted: run pymobiledevice3 mounter auto-mount --udid " + args.udid)
        async with AsyncExitStack() as stack:
            apps = await stack.enter_async_context(AppServiceService(rsd))
            dvt = await stack.enter_async_context(DvtProvider(rsd))
            process = await stack.enter_async_context(ProcessControl(dvt))
            old_pid = await process.process_identifier_for_bundle_identifier(args.runner)
            main_pid = await process.process_identifier_for_bundle_identifier(args.foreground_bundle)
            if not main_pid:
                raise RuntimeError("The microphone app must already be running; refusing to launch a new audio owner")
            try:
                pid = await process.launch(args.runner, kill_existing=False,
                    environment={"USE_PORT": "8100", "WDA_PRODUCT_BUNDLE_IDENTIFIER": args.runner})
                if pid != old_pid:
                    owned_pid = pid
                print(json.dumps({"runner_pid": pid, "owned": bool(owned_pid), "launch": "DVT",
                    "microphone_pid_before": main_pid}), flush=True)
                # iOS 26.6.2 can miss XCTest's own background notification (Code 10300).
                # Activating the already-running microphone app completes the transition.
                # This does not start a new main app process; verify identity on both sides.
                await asyncio.sleep(2)
                await activate_existing(process, args.foreground_bundle, main_pid)
                print(json.dumps({"foreground_restored_pid": main_pid}), flush=True)
                client = WdaServiceClient(rsd, timeout=3)
                deadline = time.monotonic() + args.startup_seconds
                status = None
                last_error = "no response"
                while time.monotonic() < deadline and not stopped.is_set():
                    try:
                        candidate = await asyncio.wait_for(client.get_status(), timeout=4)
                        if candidate.get("value", {}).get("ready") is True:
                            status = candidate
                            break
                        last_error = "WDA responded but is not ready"
                        await asyncio.sleep(0.5)
                    except Exception as exc:
                        last_error = str(exc)
                        await asyncio.sleep(0.5)
                if status is None:
                    raise RuntimeError("WDA not ready; inspect runner PID, device logs and screenshot for exit or authorization prompt. " + last_error)
                forward = await asyncio.create_subprocess_exec(sys.executable, "-m", "pymobiledevice3",
                    "usbmux", "forward", str(args.port), "8100", "--udid", args.udid)
                def local_status():
                    with urllib.request.urlopen(f"http://127.0.0.1:{args.port}/status", timeout=2) as response:
                        return json.load(response).get("value", {}).get("ready") is True
                local_ready = False
                for _ in range(20):
                    if forward.returncode is not None:
                        raise RuntimeError("USB forward exited")
                    try:
                        local_ready = await asyncio.to_thread(local_status)
                    except (OSError, ValueError):
                        pass
                    if local_ready:
                        break
                    await asyncio.sleep(0.2)
                if not local_ready:
                    raise RuntimeError("Local USB forward did not become ready")
                print(json.dumps({"ready": True, "udid": args.udid, "runner": args.runner,
                    "url": f"http://127.0.0.1:{args.port}", "status": status}), flush=True)
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=args.seconds)
                except TimeoutError:
                    pass
            finally:
                if forward and forward.returncode is None:
                    forward.terminate()
                    try:
                        await asyncio.wait_for(forward.wait(), 5)
                    except TimeoutError:
                        forward.kill()
                        await forward.wait()
                if owned_pid:
                    # Only this invocation's separate runner process. Never the microphone app.
                    remaining = owned_pid
                    async with DvtProvider(rsd) as dvt:
                        async with ProcessControl(dvt) as process:
                            remaining = await asyncio.wait_for(process.process_identifier_for_bundle_identifier(args.runner), 3)
                            if remaining == owned_pid:
                                await asyncio.wait_for(apps.send_signal_to_process(owned_pid, signal.SIGTERM), 8)
                            for _ in range(20):
                                remaining = await asyncio.wait_for(process.process_identifier_for_bundle_identifier(args.runner), 3)
                                if remaining != owned_pid:
                                    break
                                await asyncio.sleep(0.25)
                    print(json.dumps({"runner_stopped_pid": owned_pid, "verified_stopped": remaining != owned_pid}), flush=True)
                    if remaining == owned_pid:
                        raise RuntimeError("Owned runner did not stop after SIGTERM")
    print(json.dumps({"closed": True}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--udid", required=True)
    parser.add_argument("--foreground-bundle", default="com.rawleystanhope.rlmadk")
    parser.add_argument("--runner", default="com.rawleystanhope.wda.xctrunner")
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--startup-seconds", type=int, default=300)
    opts = parser.parse_args()
    if not 1 <= opts.seconds <= 3600 or not 1 <= opts.startup_seconds <= 300:
        parser.error("seconds must be 1..3600 and startup-seconds 1..300")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main(opts))
