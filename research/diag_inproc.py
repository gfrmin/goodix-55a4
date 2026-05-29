#!/usr/bin/env python3
"""Verify in-process TLS decryption + whether the sensor returns real pixels.

Explicit sequence (no run_driver, no finger) up to the first calibration image,
using the in-process TLS-PSK server from capture_inproc. Dumps the decrypted
plaintext stats so we can tell "blank sensor frame" from "bad decrypt".
"""
import sys

VENDOR = "<repo>/vendor/goodix-fp-dump"
if VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)

import goodix          # noqa: E402
import tool            # noqa: E402
import capture_inproc as ci  # noqa: E402  (SharedTLS / fakes; does not self-run)

d = ci.d
FDT_MODE = bytes.fromhex("0d018b0084008c0088008096809180928085808c8086")
GET_IMG = bytes.fromhex("01008b0084008c008800")


def main() -> int:
    shared = ci.SharedTLS(d.PSK)
    server = ci._FakeServer(shared)
    client = ci._FakeSocket(shared)

    device = d.init_device(0x55a4)
    print(f"[diag2] firmware: {device.firmware_version()}", flush=True)
    if not d.check_psk(device):
        d.write_psk(device)

    device.reset(True, False, 20)
    device.read_sensor_register(0x0000, 4)
    device.nop()
    device.read_otp()
    device.pov_image_check()

    client.connect(("localhost", 4433))
    tool.connect_device(device, client)
    print(f"[diag2] handshake done; cipher={shared.obj.cipher()}", flush=True)

    print(f"[diag2] upload_config_mcu -> {device.upload_config_mcu(d.DEVICE_CONFIG)}",
          flush=True)
    device.set_drv_state()
    device.set_drv_state()
    device.mcu_get_pov_image()
    device.mcu_switch_to_fdt_mode(FDT_MODE, True)

    ct = device.mcu_get_image(GET_IMG, goodix.FLAGS_TRANSPORT_LAYER_SECURITY_DATA)
    print(f"[diag2] ciphertext reply len={len(ct)} head={bytes(ct[:24]).hex()}", flush=True)
    client.sendall(ct[9:])
    pt = bytes(server.stdout.read(7684))
    print(f"[diag2] DECRYPTED plaintext: len={len(pt)} nonzero={sum(1 for b in pt if b)} "
          f"min={min(pt) if pt else 0} max={max(pt) if pt else 0}", flush=True)
    print(f"[diag2] plaintext head(48): {pt[:48].hex()}", flush=True)
    print(f"[diag2] plaintext tail(16): {pt[-16:].hex()}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BaseException as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(1)
