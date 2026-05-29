#!/usr/bin/env python3
"""M0 capture via an IN-PROCESS Python TLS-PSK server (no openssl subprocess).

The legacy driver relays the device's TLS bytes through `openssl s_server` and
reads decrypted plaintext from its stdout. On OpenSSL 3.6 that relay produces no
usable session. Here we replace it with an in-process `ssl` PSK server over a
MemoryBIO pair, feeding it the SAME bytes `run_driver` already sends to its
socket — via drop-in fakes for `socket.socket()` and the openssl `Popen` — so we
don't rewrite the protocol sequence and we get real TLS errors if it fails.

NO flash. Same no-flash firmware guard as capture_m0.py. Frames -> vault.
Run: cd vendor/goodix-fp-dump && sudo PYTHONPATH=$PWD \
     <repo>/.venv/bin/python .../research/capture_inproc.py
"""
import datetime as _dt
import os
import re
import socket as _socket_mod
import ssl
import sys

VENDOR = "<repo>/vendor/goodix-fp-dump"
VAULT = "$GOODIX_VAULT/frames"
PRODUCT = 0x55a4

if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

import driver_5503 as d  # noqa: E402


class SharedTLS:
    """In-process TLS-1.2 PSK *server* over MemoryBIOs."""

    def __init__(self, psk: bytes):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        # CERT_NONE is mandatory for TLS-PSK (no certificates exist in PSK
        # suites; auth IS the pre-shared key). This is an in-process MemoryBIO
        # decrypting a local USB device's stream — no network, no MITM surface —
        # and matches the design's accepted threat model (known/zero PSK).
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        for spec in ("PSK:@SECLEVEL=0", "ALL:@SECLEVEL=0"):
            try:
                ctx.set_ciphers(spec)
                break
            except ssl.SSLError:
                continue
        ctx.set_psk_server_callback(lambda *a: psk)  # any identity -> our PSK
        self.inbound = ssl.MemoryBIO()
        self.outbound = ssl.MemoryBIO()
        self.obj = ctx.wrap_bio(self.inbound, self.outbound, server_side=True)
        self.hs_done = False
        self.last_err = None

    def net_in(self, data: bytes) -> None:
        self.inbound.write(data)
        if not self.hs_done:
            try:
                self.obj.do_handshake()
                self.hs_done = True
                print("[inproc] TLS handshake complete; "
                      f"cipher={self.obj.cipher()}", flush=True)
            except ssl.SSLWantReadError:
                pass
            except ssl.SSLError as e:
                self.last_err = repr(e)
                print(f"[inproc] handshake SSLError: {e!r}", flush=True)
                raise

    def net_out(self, n: int) -> bytes:
        return self.outbound.read(n)

    def read_plain(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.obj.read(n - len(buf))
            except ssl.SSLWantReadError:
                break
            except ssl.SSLError as e:
                self.last_err = repr(e)
                print(f"[inproc] read SSLError: {e!r}", flush=True)
                break
            if not chunk:
                break
            buf += chunk
        return buf


class _FakeSocket:
    def __init__(self, shared): self.s = shared
    def connect(self, *_a): pass
    def settimeout(self, *_a): pass
    def sendall(self, data): self.s.net_in(bytes(data))
    def recv(self, n): return self.s.net_out(n)
    def close(self): pass


class _FakeStdout:
    def __init__(self, shared): self.s = shared
    def read(self, n): return self.s.read_plain(n)
    def flush(self): pass


class _FakeServer:
    def __init__(self, shared): self.stdout = _FakeStdout(shared)
    def terminate(self): pass


_holder = {}
_real_popen = d.subprocess.Popen


def _fake_popen(args, **kw):
    if isinstance(args, (list, tuple)) and args and "openssl" in str(args[0]):
        shared = SharedTLS(d.PSK)
        _holder["shared"] = shared
        print("[inproc] using in-process TLS-PSK server (no openssl)", flush=True)
        return _FakeServer(shared)
    return _real_popen(args, **kw)


def _fake_socket(*_a, **_k):
    return _FakeSocket(_holder["shared"])


def session_dir() -> str:
    base = _dt.date.today().isoformat()
    name, n = base, 1
    while os.path.exists(os.path.join(VAULT, name)):
        n += 1
        name = f"{base}-{n}"
    path = os.path.join(VAULT, name)
    os.makedirs(path, exist_ok=False)
    return path


def main() -> int:
    d.subprocess.Popen = _fake_popen
    d.socket.socket = _fake_socket

    device = d.init_device(PRODUCT)
    firmware = device.firmware_version()
    print(f"[inproc] firmware: {firmware}", flush=True)
    if not re.fullmatch(d.WORKING_FIRMWARE, firmware):
        print(f"[inproc] REFUSING: {firmware!r} != WORKING_FIRMWARE; no flash.",
              file=sys.stderr, flush=True)
        return 2

    if not d.check_psk(device):
        print("[inproc] writing zero-PSK (no firmware change)...", flush=True)
        if not d.write_psk(device):
            return 3

    out = session_dir()
    os.chdir(out)
    print(f"[inproc] session dir: {out}", flush=True)
    print("[inproc] capturing calibration frames (no finger needed)...", flush=True)

    d.run_driver(device)

    latest = os.path.join(VAULT, "latest")
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(os.path.basename(out), latest)
    except OSError:
        pass
    print(f"[inproc] DONE. frames: {sorted(os.listdir(out))}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    except BaseException as exc:  # noqa: BLE001
        import traceback
        print("[inproc] EXCEPTION:\n" + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)),
            flush=True)
        rc = 1
    sys.exit(rc)
