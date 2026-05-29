#!/usr/bin/env python3
"""M0 capture — Goodix 27c6:55a4, NO-FLASH path.

This unit already runs GF3208/GF3258_RTSEC_APP_10062, which is the `5503`
driver's WORKING_FIRMWARE. So we drive it with driver_5503's primitives pointed
at the 55a4 USB PID, calling ONLY the safe steps:

    init_device -> firmware_version -> (write_psk if needed) -> run_driver

It deliberately NEVER references erase_firmware / update_firmware, and refuses to
run unless the firmware matches WORKING_FIRMWARE -- no flash/erase path reachable.

The TLS-1.2/PSK-cipher fix for modern OpenSSL lives in the vendored
driver_5503.py (run_driver's openssl s_server args).

Frames are written into the out-of-repo vault:
    $GOODIX_VAULT/frames/<session>/{clear-0,clear-1,fingerprint-0}.pgm

Run as root (USB access), with the vendor dir importable:
    cd vendor/goodix-fp-dump && sudo PYTHONPATH=$PWD \
        <repo>/.venv/bin/python \
        <repo>/research/capture_m0.py
"""
import datetime as _dt
import os
import re
import subprocess
import sys
import time

# NOTE: hardcode the real user home. Under sudo, os.path.expanduser("~") -> /root,
# which would dump biometric frames into root's home instead of the user vault.
VENDOR = "<repo>/vendor/goodix-fp-dump"
VAULT = "$GOODIX_VAULT/frames"
PRODUCT = 0x55a4

if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

import driver_5503 as d  # noqa: E402  (after sys.path tweak)


def session_dir() -> str:
    base = _dt.date.today().isoformat()
    name, n = base, 1
    while os.path.exists(os.path.join(VAULT, name)):
        n += 1
        name = f"{base}-{n}"
    path = os.path.join(VAULT, name)
    os.makedirs(path, exist_ok=False)
    return path


def clear_stale_tls_server() -> None:
    """driver_5503 binds openssl s_server on :4433. If a previous run was
    SIGKILLed, its `finally: tls_server.terminate()` never ran and the orphan
    keeps :4433 bound -> the next run's decryptor can't bind -> empty frame.

    Kill the holder by PORT (fuser), NOT by `pkill -f "openssl s_server"`:
    `-f` matches the full cmdline, so it would also kill any parent shell whose
    command line happens to contain that string (which suicides the job)."""
    subprocess.run(["fuser", "-k", "-9", "4433/tcp"],
                   check=False, capture_output=True)
    time.sleep(0.3)


def main() -> int:
    clear_stale_tls_server()
    device = d.init_device(PRODUCT)

    firmware = device.firmware_version()
    print(f"[capture] firmware: {firmware}", flush=True)

    # HARD no-flash guard: only proceed on the known working firmware. If this
    # fails we abort -- we never fall through to any erase/flash branch.
    if not re.fullmatch(d.WORKING_FIRMWARE, firmware):
        print(f"[capture] REFUSING: firmware {firmware!r} is not the expected "
              f"WORKING_FIRMWARE ({d.WORKING_FIRMWARE!r}). Aborting WITHOUT any "
              f"flash/erase.", file=sys.stderr, flush=True)
        return 2

    try:
        print(f"[capture] iap: {device.get_iap_version(25)}", flush=True)
    except Exception as e:  # noqa: BLE001 - informational only
        print(f"[capture] iap read failed (non-fatal): {e}", flush=True)

    valid_psk = d.check_psk(device)
    print(f"[capture] valid PSK: {valid_psk}", flush=True)
    if not valid_psk:
        print("[capture] writing zero-PSK white-box (no firmware change)...", flush=True)
        if not d.write_psk(device):
            print("[capture] PSK write FAILED", file=sys.stderr, flush=True)
            return 3

    out = session_dir()
    os.chdir(out)  # tool.write_pgm writes relative filenames -> land in the vault
    print(f"[capture] session dir: {out}", flush=True)
    print("[capture] capturing calibration frames (no finger needed)...", flush=True)

    d.run_driver(device)  # writes clear-0/clear-1, then blocks for finger -> fingerprint-0

    latest = os.path.join(VAULT, "latest")
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(os.path.basename(out), latest)
    except OSError as e:
        print(f"[capture] could not update latest symlink: {e}", flush=True)

    print(f"[capture] DONE. frames in {out}: {sorted(os.listdir(out))}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
