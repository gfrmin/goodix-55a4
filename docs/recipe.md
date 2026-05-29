# Recipe: reverse-engineering a fingerprint reader into a Linux driver

> Seed for the longer-term "AI-assisted RE → drivers" idea. Filled in as the `55a4` work
> teaches us what generalizes. Driver-first means this is a **byproduct**, not a goal —
> capture the reusable steps here as they prove out, don't build tooling speculatively.

## The general shape (from this project + the field)

1. **Identify & triage.** `lsusb` → vendor/product ID. Check `libfprint`'s supported &
   unsupported lists and existing community efforts *before* reversing anything — most of
   the work may already exist.
2. **Decide: reverse, or inherit?** If the protocol is already reversed (as for Goodix
   `55x4`), inherit captures/dissectors and skip the Windows-driver teardown. Only reach
   for Ghidra/x64dbg + usbmon/Wireshark when nothing exists.
3. **Capture-centric loop.** `usbmon` + Wireshark, `.pcapng`, repeat each action many
   times, diff. Lua dissector with hot-reload. Keep golden logs.
4. **Know what the device actually returns.** Match-on-chip (device says yes/no) vs
   match-on-host (device returns an image; *you* build recognition). This decides how much
   work the driver is. Fingerprint readers are usually the latter.
5. **Reuse the OS stack.** For fingerprints: target a `libfprint` `FpImageDevice` so its
   built-in matcher + `fprintd` + `pam_fprintd` give you enrollment and PAM for free.
   Don't hand-roll PAM.
6. **Measure before trusting** (FAR/FRR), and treat host-side biometrics as a convenience
   factor with a password fallback.

## Where AI plausibly helps (to test, not assume)

- Diffing/labeling capture sets and proposing packet-field structure.
- Drafting dissectors and driver state machines from documented field tables.
- Tuning image preprocessing / matcher parameters against an eval harness.

Record here which of these actually paid off on the `55a4`, with examples.
