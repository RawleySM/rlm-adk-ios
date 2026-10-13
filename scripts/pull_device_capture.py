#!/usr/bin/env python3
"""Copy closed native audio segments over paired USB without modifying the phone.

An optional Unix time window selects complete overlapping segments, not resampled
audio. The full original manifest and a receipt make partial-window proof explicit.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path


def unix(value):
    return float(value) + 978307200 if value is not None else None


async def main(args):
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.house_arrest import HouseArrestService
    args.out.mkdir(parents=True, exist_ok=True)
    async with await create_using_usbmux(serial=args.udid, autopair=False) as device:
        async with await HouseArrestService.create(device, args.bundle) as files:
            raw = await files.get_file_contents('/Documents/captures.json')
            (args.out / 'captures.json').write_bytes(raw)
            capture = next(c for c in json.loads(raw) if c['captureId'] == args.capture)
            receipt = {'capture_id': args.capture, 'udid': args.udid,
                'proof_scope': 'selected closed segments' if args.since or args.until else 'all closed segments',
                'since_unix': args.since, 'until_unix': args.until,
                'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'files': [],
                'gaps': capture.get('gaps', []), 'capture_closed': capture.get('closedAt') is not None}
            directory = args.out / args.capture
            directory.mkdir(exist_ok=True)
            for seg in sorted(capture['segments'], key=lambda s: s['index']):
                start, end = unix(seg.get('startedAt')), unix(seg.get('endedAt'))
                if end is None or (args.since is not None and end < args.since) or (args.until is not None and start > args.until):
                    continue
                name = seg['filename']
                if Path(name).name != name:
                    raise ValueError('segment filename is not a basename')
                data = await files.get_file_contents(f'/Documents/captures/{args.capture}/{name}')
                digest = hashlib.sha256(data).hexdigest()
                if digest != seg['sha256'] or len(data) != seg['bytes']:
                    raise ValueError(f'segment {seg["index"]} differs from manifest')
                path = directory / name
                if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError(f'existing local file differs: {path}')
                path.write_bytes(data)
                receipt['files'].append({**seg, 'started_unix': start, 'ended_unix': end, 'path': str(path), 'hash_verified': True})
            if not receipt['files']:
                raise RuntimeError('no closed segments in the selected window; wait for rotation then retry')
            (args.out / 'pull-receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
            print(json.dumps({'files': len(receipt['files']), 'indexes': [s['index'] for s in receipt['files']], 'receipt': str(args.out/'pull-receipt.json')}))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--udid', required=True)
    p.add_argument('--bundle', default='com.rawleystanhope.rlmadk')
    p.add_argument('--capture', required=True)
    p.add_argument('--since', type=float)
    p.add_argument('--until', type=float)
    p.add_argument('--out', type=Path, required=True)
    asyncio.run(asyncio.wait_for(main(p.parse_args()), timeout=120))
