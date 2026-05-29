#!/usr/bin/env python3
"""M0 read-only probe: USB init + firmware version + PSK status for 27c6:55a4.

Bypasses driver_55x4.main()'s anti-bot prompt and, crucially, does NOT run the
firmware-flash branches or capture an image. It only issues read commands
(nop, firmware_version, preset_psk_read) so we can see — before committing to a
capture run — whether this unit's firmware already matches the capture target
(no flash needed) or would trigger an erase/reflash.

Run as root (USB access):  sudo /path/.venv/bin/python research/probe_m0.py
Must run with CWD = vendor/goodix-fp-dump (imports goodix/protocol/driver_55x4).
"""
import re
import sys

import goodix
import protocol
import driver_55x4 as d


def main() -> int:
    print(f"Target capture firmware : {d.TARGET_FIRMWARE}")
    print(f"IAP firmware            : {d.IAP_FIRMWARE}")
    print(f"Valid-firmware pattern  : {d.VALID_FIRMWARE}")
    print("-" * 60)

    device = goodix.Device(0x55a4, protocol.USBProtocol)
    device.nop()

    firmware = device.firmware_version()
    print(f"Device firmware         : {firmware}")

    valid_psk = d.check_psk(device)
    print(f"Valid PSK (zero-PSK)    : {valid_psk}")

    print("-" * 60)
    if re.fullmatch(d.TARGET_FIRMWARE, firmware):
        print("PATH: firmware already == TARGET -> capture runs WITHOUT flashing.")
        if not valid_psk:
            print("      (PSK not yet set: capture will write the zero-PSK white-box once.)")
    elif re.fullmatch(d.IAP_FIRMWARE, firmware):
        print("PATH: device in IAP -> capture run WOULD FLASH target firmware.")
    elif re.fullmatch(d.VALID_FIRMWARE, firmware):
        print("PATH: other valid firmware -> capture run WOULD ERASE current app, then reflash.")
    else:
        print("PATH: UNRECOGNIZED firmware -> capture would refuse (ValueError).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
