"""Send only the RemotePairing opening handshake to host:port and print what the device answers.

No pairing, pair-verify or tunnel is attempted: the device's handshake reply identifies the service.
"""
import asyncio, json, sys
from pymobiledevice3.remote.tunnel_service import RemotePairingTunnelService

async def main(host: str, port: int) -> None:
    svc = RemotePairingTunnelService("probe", host, port)
    svc._reader, svc._writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
    try:
        await asyncio.wait_for(svc._attempt_pair_verify(), 15)
        info = svc.handshake_info
        print(json.dumps(info, indent=1, default=str)[:3000])
    except Exception as exc:
        print(f"handshake failed: {type(exc).__name__}: {exc}")
    finally:
        svc._writer.close()

asyncio.run(main(sys.argv[1], int(sys.argv[2])))
