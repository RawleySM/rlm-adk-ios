#!/usr/bin/env python3
"""Physical prayer trial against a separately configured REAL backend/intake.

Start the run-owned services first. This script operates the real UI, preserves the original
backend and Quiet setting, and never substitutes a file upload for capture.

State safety: the original backend URL and Quiet flag are written to ``<out>/restore-state.json``
before anything is mutated; prayer ownership is recorded there the moment Begin is tapped (ownership
means "no prayer was open before our tap", so a later snapshot failure cannot lose it). Prayer,
backend and Quiet are restored in independent steps, and SIGTERM/SIGINT run the same restoration
and still write the receipt. A pre-existing prayer is never ended. Publication is awaited for a
bounded, configurable time: ``finalized`` and ``published`` are separate checks and an unsettled
wait yields outcome ``pending``, which is not acceptance.

Run with ``scripts/`` on the import path (``cd scripts && python3 device_prayer_eval.py ...``).
"""
import argparse
import json
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from device_eval.receipt import Cancelled, Receipt, install_cancel_handlers
from device_eval.ui import require_no_alert, snapshot, tap_tab
from device_eval.wda import Wda

TERMINAL_INTAKE = ("published", "failed", "excluded", "discarded")


def request(base, path, data=None):
    body = None if data is None else json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(base.rstrip('/') + path, body,
            headers={'Content-Type': 'application/json'}), timeout=10) as response:
        return json.load(response)


def fresh(base, install, http=request, timeout=15.0):
    path = f'/devices/{install}'
    previous = http(base, path + '/snapshot').get('client_time') or 0
    http(base, path + '/commands', {'kind': 'snapshot', 'args': {}})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = http(base, path + '/snapshot')
        if (state.get('client_time') or 0) > previous:
            return state
        time.sleep(.3)
    raise RuntimeError('No fresh phone snapshot from selected backend')


def change_backend(wda, url):
    tap_tab(wda, 'Diagnostics')
    field = wda.by_id('backendURLField')
    wda.click(field)
    wda.request('POST', f'/session/{wda.session_id}/element/{field.id}/clear', {})
    wda.type_text(url)
    value = wda.attribute(field, 'value')
    if value != url:
        raise RuntimeError(f'backend field reads {value!r} after typing {url!r}')
    wda.click(wda.find('accessibility id', 'Apply'))
    tap_tab(wda, 'Portal')


class RestoreState:
    """Durable record of what must be put back, written before each mutation."""

    def __init__(self, path: Path):
        self.path = path
        self.data = {'original_backend': None, 'original_quiet': None, 'backend_changed': False, 'quiet_changed': False,
                     'prayer_owned': False, 'capture_id': None, 'restored': {}}
        self.save()

    def set(self, **kv):
        self.data.update(kv)
        self.save()

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2) + '\n')


def trial(args, http=request, player_factory=subprocess.Popen, sleep=time.sleep):
    args.out.mkdir(parents=True, exist_ok=True)
    r = Receipt('physical-prayer-trial', vars(args))
    w = Wda(args.wda, session_id=args.session_id)
    st = RestoreState(args.out / 'restore-state.json')
    player = None
    install_cancel_handlers()
    try:
        if w.locked():
            raise RuntimeError('Phone is locked')
        if w.app_state(args.bundle) != 4:
            raise RuntimeError('Attach the already-running app first')
        require_no_alert(w, r, args.out, 'start')
        tap_tab(w, 'Portal')
        if w.label(w.by_id('prayerButton')) != 'Begin prayer':
            raise RuntimeError('Existing prayer is open; this trial never ends a prayer it did not begin')
        original_quiet = w.label(w.by_id('quietButton')) == 'Wake'
        r.action('preflight', app=w.active_app_info(), quiet=original_quiet)
        tap_tab(w, 'Diagnostics')
        original_backend = w.attribute(w.by_id('backendURLField'), 'value')
        if not original_backend or original_backend.rstrip('/') == args.test_backend.rstrip('/'):
            raise RuntimeError('Test backend must differ from the current backend (restore the original first if a previous run left it)')
        st.set(original_backend=original_backend, original_quiet=original_quiet)  # durable before any mutation
        r.action('original_state_recorded', path=str(st.path), backend=original_backend, quiet=original_quiet)
        tap_tab(w, 'Portal')
        if not original_quiet:
            st.set(quiet_changed=True)
            w.click(w.by_id('quietButton'))
        st.set(backend_changed=True)
        change_backend(w, args.test_backend)
        before = fresh(args.test_backend, args.install_id, http)
        (args.out / 'phone-before.json').write_text(json.dumps(before, indent=2) + '\n')
        prior_ids = {c['capture_id'] for c in before['snapshot']['captures']}
        if before['snapshot']['praying'] or not before['snapshot']['engine_running']:
            raise RuntimeError('phone reports praying or engine stopped before Begin')
        # Ownership is decided by the tap, not by the next snapshot: no prayer was open above.
        st.set(prayer_owned=True)
        w.click(w.by_id('prayerButton'))
        r.action('begin_prayer_tapped')
        begun = fresh(args.test_backend, args.install_id, http)
        candidates = [c for c in begun['snapshot']['captures'] if c['kind'] == 'prayer' and c['capture_id'] not in prior_ids and not c['closed']]
        if len(candidates) != 1:
            raise RuntimeError('Cannot uniquely identify trial capture')
        capture_id = candidates[0]['capture_id']
        st.set(capture_id=capture_id)
        r.action('begin_prayer', capture_id=capture_id)
        snapshot(w, r, args.out, 'prayer-begun')
        command = [sys.executable, str(Path(__file__).with_name('device_eval.py')), 'audio', 'play-record', '--fixture', str(args.fixture), '--out', str(args.out / 'acoustic'), '--sink', args.sink, '--stream-volume', str(args.volume), '--pre-roll', '3', '--tail', '5']
        with (args.out / 'play-record.log').open('w') as log:
            player = player_factory(command, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + args.replay_timeout
            while player.poll() is None:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Acoustic trial exceeded finite deadline')
                label = w.label(w.by_id('prayerButton'))
                if label != 'End prayer':
                    raise RuntimeError('Prayer ended during replay or pause')
                s = fresh(args.test_backend, args.install_id, http)
                if not s['snapshot']['engine_running']:
                    raise RuntimeError('Native audio engine stopped')
                r.action('during_replay', prayer_label=label, buffer_age=s['snapshot']['last_buffer_age_s'], engine_running=True)
                print(json.dumps({'phase': 'replaying', 'capture_id': capture_id, 'prayer_still_open': True}), flush=True)
                sleep(args.poll_interval)
            r.check('acoustic_transport_exit_ok', player.returncode == 0, reason=f'play-record rc={player.returncode}; transport only, inspect play-record log')
        current = fresh(args.test_backend, args.install_id, http)
        own_open = any(c['capture_id'] == capture_id and not c['closed'] for c in current['snapshot']['captures'])
        if not r.check('own_prayer_still_open_before_end', own_open):
            st.set(prayer_owned=False)
            raise RuntimeError('Owned prayer already closed; refusing a toggle that could begin another')
        snapshot(w, r, args.out, 'prayer-after-pauses')
        w.click(w.by_id('prayerButton'))
        st.data['restored']['prayer'] = 'ended_explicitly'
        st.set(prayer_owned=False)
        r.action('end_prayer', capture_id=capture_id, note='explicit End prayer tap; long pauses never end a prayer')
        deadline = time.monotonic() + args.publish_timeout
        status = {}
        seen = []
        while True:
            status = http(args.test_backend, f'/prayer/captures/{capture_id}')
            (args.out / 'intake-status.json').write_text(json.dumps(status, indent=2) + '\n')
            if status.get('status') not in seen:
                seen.append(status.get('status'))
            if status.get('status') in TERMINAL_INTAKE or time.monotonic() >= deadline:
                break
            print(json.dumps({'phase': 'intake', 'capture_id': capture_id, 'status': status.get('status')}), flush=True)
            sleep(args.intake_poll_interval)
        final_status = status.get('status')
        finalized = final_status in ('finalized', 'published') or 'finalized' in seen
        r.check('real_intake_finalized', finalized, reason=f'status {final_status}', statuses_seen=seen)
        r.check('real_intake_not_failed', final_status not in ('failed', 'excluded', 'discarded'), reason=f'status {final_status}')
        if final_status == 'published':
            r.check('real_intake_published', True, prayer_id=status.get('prayer_id'))
        else:
            r.pending(f'intake status {final_status!r} after {args.publish_timeout}s; publication not observed, rerun status later')
        r.action('intake_result', capture_id=capture_id, status=status)
        after = fresh(args.test_backend, args.install_id, http)
        (args.out / 'phone-after.json').write_text(json.dumps(after, indent=2) + '\n')
        final = next(c for c in after['snapshot']['captures'] if c['capture_id'] == capture_id)
        r.check('phone_capture_closed_nonempty', final['closed'] and final['segments'] > 0 and final['bytes'] > 0, capture=final)
        snapshot(w, r, args.out, 'prayer-ended')
    except (KeyboardInterrupt, Cancelled) as exc:
        r.cancelled(f'{exc.__class__.__name__}: {exc}')
        print(json.dumps({'cancelled': str(exc)}), flush=True)
    except Exception as exc:
        r.check('trial_completed', False, reason=f'{exc.__class__.__name__}: {exc}')
        print(type(exc).__name__, str(exc), flush=True)
    finally:
        restore(args, r, w, st, player, http)
        r.data['result'] = {'capture_id': st.data['capture_id'], 'original_backend': st.data['original_backend'], 'restore_state': st.data}
        r.finish()
        r.write(args.out / 'prayer-trial-receipt.json')
        print(json.dumps({'outcome': r.data['outcome'], 'reasons': r.data['reasons'], 'capture_id': st.data['capture_id']}), flush=True)
    return r.exit_code


def restore(args, r, w, st, player, http):
    """Each restoration is independent: a failure in one never skips the others."""
    try:
        if player and player.poll() is None:
            player.send_signal(signal.SIGINT)  # the harness cleans up its remote children on SIGINT
            try:
                player.wait(timeout=30)
            except subprocess.TimeoutExpired:
                player.terminate()
                try:
                    player.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    player.kill()
                    player.wait()
            r.action('player_stopped', rc=player.returncode)
    except Exception as exc:
        r.check('player_stopped', False, reason=str(exc))
    if st.data['prayer_owned']:
        try:
            if w.label(w.by_id('prayerButton')) == 'End prayer':
                w.click(w.by_id('prayerButton'))
                r.action('cleanup_end_owned_prayer', capture_id=st.data['capture_id'])
            st.set(prayer_owned=False)
            st.data['restored']['prayer'] = True
            st.save()
        except Exception as exc:
            r.check('own_prayer_ended', False, reason=str(exc))
    if st.data['backend_changed'] and st.data['original_backend']:
        try:
            change_backend(w, st.data['original_backend'])
            st.set(backend_changed=False)
            st.data['restored']['backend'] = True
            st.save()
            r.check('original_backend_field_restored', True)
            try:
                state = fresh(st.data['original_backend'], args.install_id, http)
                r.check('original_backend_reconnected', state.get('connected') is True, reason='original backend did not report the phone connected')
            except Exception as exc:
                r.check('original_backend_reconnected', False, reason=str(exc))
        except Exception as exc:
            r.check('original_backend_field_restored', False, reason=f'{exc}; restore by hand from {st.path}')
    if st.data['quiet_changed'] and st.data['original_quiet'] is not None:
        try:
            tap_tab(w, 'Portal')
            current_quiet = w.label(w.by_id('quietButton')) == 'Wake'
            if current_quiet != st.data['original_quiet']:
                w.click(w.by_id('quietButton'))
            ok = (w.label(w.by_id('quietButton')) == 'Wake') == st.data['original_quiet']
            r.check('quiet_restored', ok)
            if ok:
                st.set(quiet_changed=False)
                st.data['restored']['quiet'] = True
                st.save()
        except Exception as exc:
            r.check('quiet_restored', False, reason=f'{exc}; restore by hand from {st.path}')
    try:
        r.action('final_app', app=w.active_app_info())
    except Exception as exc:
        r.action('final_app_unavailable', error=str(exc))


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--wda', default='http://127.0.0.1:18100')
    p.add_argument('--bundle', default='com.rawleystanhope.rlmadk')
    p.add_argument('--session-id', required=True)
    p.add_argument('--install-id', required=True)
    p.add_argument('--test-backend', required=True)
    p.add_argument('--fixture', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--sink', default='alsa_output.usb-1130_USB_AUDIO-00.analog-stereo')
    p.add_argument('--volume', type=float, default=.5)
    p.add_argument('--replay-timeout', type=float, default=400.0)
    p.add_argument('--poll-interval', type=float, default=10.0)
    p.add_argument('--publish-timeout', type=float, default=120.0, help='bounded wait for intake publication; unsettled -> outcome pending')
    p.add_argument('--intake-poll-interval', type=float, default=5.0)
    return p


if __name__ == '__main__':
    raise SystemExit(trial(build_parser().parse_args()))
