#!/usr/bin/env python3
"""Experiment: drive capture with the 55x4 (88x108) image config, NO flash.

Our unit is 55a4-PID hardware running 5503 firmware. The 5503 image config gave
all-zero frames (wrong array params, theory). Here we run driver_55x4's
run_driver sequence (its DEVICE_CONFIG, FDT byte strings, 88x108 dims) over the
in-process TLS-PSK server, WITHOUT ever calling driver_55x4.main (which flashes).
init/PSK helpers come from driver_5503; firmware guard still refuses unless the
firmware is the known 5503 working firmware.
"""
import datetime as _dt
import os
import re
import sys

VENDOR = "<repo>/vendor/goodix-fp-dump"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault  # noqa: E402  (vault root + external-volume guard)

VAULT = vault.frames()

if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

import driver_5503 as d5   # noqa: E402  init_device / check_psk / write_psk / WORKING_FIRMWARE
import driver_55x4 as d4   # noqa: E402  run_driver + DEVICE_CONFIG + 88x108 dims
import capture_inproc as ci  # noqa: E402  SharedTLS + fakes (does not self-run)

_holder = {}


def _fake_popen(args, **kw):
    if isinstance(args, (list, tuple)) and args and "openssl" in str(args[0]):
        sh = ci.SharedTLS(d4.PSK)
        _holder["s"] = sh
        print("[55x4cfg] using in-process TLS-PSK (no openssl)", flush=True)
        return ci._FakeServer(sh)
    return ci._real_popen(args, **kw)


def _fake_socket(*_a, **_k):
    return ci._FakeSocket(_holder["s"])


def session_dir() -> str:
    # Optional label arg -> <vault>/frames/<label>[-N]; else dated.
    base = sys.argv[1] if len(sys.argv) > 1 else "55x4cfg-" + _dt.date.today().isoformat()
    name, n = base, 1
    while os.path.exists(os.path.join(VAULT, name)):
        n += 1
        name = f"{base}-{n}"
    path = os.path.join(VAULT, name)
    os.makedirs(path)
    return path


def main() -> int:
    d4.subprocess.Popen = _fake_popen
    d4.socket.socket = _fake_socket

    device = d5.init_device(0x55a4)
    fw = device.firmware_version()
    print(f"[55x4cfg] firmware: {fw}", flush=True)
    # NOTE: firmware_version() reads flakily on this unit — the 3rd model digit
    # varies across reads (GF3208 / GF3258 / GF3268), all "..._RTSEC_APP_10062".
    # We only call run_driver (no flash path exists here at all), so guard with
    # the tolerant GF32xx-app family pattern rather than the exact string.
    if not re.fullmatch(d5.VALID_FIRMWARE, fw):
        print(f"[55x4cfg] REFUSING: {fw!r} not a GF32xx app firmware; no flash.",
              file=sys.stderr, flush=True)
        return 2
    if not d5.check_psk(device):
        d5.write_psk(device)

    out = session_dir()
    os.chdir(out)
    print(f"[55x4cfg] session dir: {out} (88x108 config)", flush=True)

    # driver_55x4.run_driver: clear-0/clear-1 (no finger), then 'Waiting for
    # finger...' + fingerprint.pgm. We watch clear-0 for real pixels.
    d4.run_driver(device)

    print(f"[55x4cfg] DONE: {sorted(os.listdir(out))}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except BaseException as exc:  # noqa: BLE001
        import traceback
        print("[55x4cfg] EXCEPTION:\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)),
            flush=True)
        rc = 1
    sys.exit(rc)
