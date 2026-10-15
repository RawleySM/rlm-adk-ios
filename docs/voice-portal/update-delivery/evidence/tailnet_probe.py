"""TCP connect probe of the iPhone's tailnet address: which ports answer, refuse or stay silent."""
import asyncio, sys, time
HOST = sys.argv[1]
PORTS = sorted(set(list(range(1, 1025)) + list(range(49152, 65536)) + [62078, 5353, 3689, 7000, 8080, 8443]))

async def probe(port, sem, out):
    async with sem:
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(HOST, port), 3.0)
            out["open"].append(port); w.close()
        except ConnectionRefusedError:
            out["refused"] += 1
        except (asyncio.TimeoutError, OSError):
            out["silent"].append(port)

async def main():
    out = {"open": [], "refused": 0, "silent": []}
    sem = asyncio.Semaphore(400)
    t0 = time.time()
    await asyncio.gather(*(probe(p, sem, out) for p in PORTS))
    print(f"host {HOST}: {len(PORTS)} TCP ports probed (1-1024, 49152-65535, 62078, 5353, 3689, 7000, 8080, 8443) in {time.time()-t0:.0f} s")
    print(f"open: {sorted(out['open'])}")
    print(f"refused (RST): {out['refused']}")
    print(f"no answer within 3 s: {len(out['silent'])} {sorted(out['silent'])[:20]}")
asyncio.run(main())
