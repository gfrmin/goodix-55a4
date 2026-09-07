#!/usr/bin/env python3
"""M0 diagnostic — capture the exact reason the first image frame fails.

Drives driver_5503's capture prefix EXPLICITLY (copying its exact byte
sequences) up to the first `mcu_get_image`, but:
  * gives openssl s_server its own stderr pipe, drained in a thread;
  * uses select() with a timeout on the decrypted stdout so we never hang;
  * logs the raw sensor reply length and the decrypted-plaintext length.

Prints everything to stdout (read the background task's output file). NO flash,
NO finger, writes no files.
"""
import os
import select
import socket
import subprocess
import sys
import threading
import time


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vault  # noqa: E402  (repo layout + vault root + volume guard)

VENDOR = vault.vendor()
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

import goodix          # noqa: E402
import tool            # noqa: E402
import driver_5503 as d  # noqa: E402

FDT_MODE = bytes.fromhex("0d018b0084008c0088008096809180928085808c8086")
GET_IMG = bytes.fromhex("01008b0084008c008800")


def p(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    # kill by PORT, not `pkill -f "openssl s_server"` (that also matches our own
    # shell command line when it contains that string -> suicides the job).
    subprocess.run(["fuser", "-k", "-9", "4433/tcp"],
                   check=False, capture_output=True)
    time.sleep(0.3)

    # Offer ALL PSK ciphers at TLS 1.2 / SECLEVEL 0 and let the device pick,
    # rather than guessing the single suite.
    server = subprocess.Popen(
        ["openssl", "s_server", "-nocert", "-psk", d.PSK.hex(),
         "-port", "4433", "-quiet", "-tls1_2", "-cipher", "PSK:@SECLEVEL=0"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err_chunks: list[bytes] = []
    threading.Thread(target=lambda: err_chunks.append(server.stderr.read()),
                     daemon=True).start()
    time.sleep(0.3)
    p("[diag] openssl s_server spawned")

    device = d.init_device(0x55a4)
    p(f"[diag] firmware: {device.firmware_version()}")
    if not d.check_psk(device):
        p("[diag] writing zero-PSK...")
        d.write_psk(device)

    device.reset(True, False, 20)
    device.read_sensor_register(0x0000, 4)
    device.nop()
    device.read_otp()
    device.pov_image_check()

    client = socket.socket()
    client.connect(("localhost", 4433))
    tool.connect_device(device, client)
    p("[diag] TLS handshake (relayed) completed")

    ok = device.upload_config_mcu(d.DEVICE_CONFIG)
    p(f"[diag] upload_config_mcu -> {ok}")
    device.set_drv_state()
    device.set_drv_state()
    device.mcu_get_pov_image()
    device.mcu_switch_to_fdt_mode(FDT_MODE, True)

    raw = device.mcu_get_image(GET_IMG, goodix.FLAGS_TRANSPORT_LAYER_SECURITY_DATA)
    p(f"[diag] sensor image reply: len={len(raw)} -> sending [9:]={len(raw) - 9} B "
      f"to openssl; head={bytes(raw[:24]).hex()}")
    client.sendall(raw[9:])

    r, _, _ = select.select([server.stdout], [], [], 5.0)
    if r:
        data = os.read(server.stdout.fileno(), 65536)
        p(f"[diag] openssl decrypted stdout: {len(data)} bytes (driver wants 7684)")
    else:
        p("[diag] TIMEOUT: openssl produced NO decrypted output in 5s "
          "(likely a TLS alert + session close).")

    time.sleep(0.4)
    p(f"[diag] openssl process poll (None=alive): {server.poll()}")
    err = b"".join(c for c in err_chunks if c)
    p(f"[diag] ---- openssl stderr ({len(err)} bytes) ----")
    p(err.decode(errors="replace") if err else "<empty>")
    p("[diag] ---- end stderr ----")

    try:
        client.close()
    finally:
        server.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
