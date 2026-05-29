# docs/protocol/ — protocol notes

Confirmed packet-field tables and **golden reference logs** (full init/handshake sequence)
for the `55a4`, plus any deltas we find versus the upstream `goodix-fp-dump` `55x4`
implementation on this specific firmware revision.

Per RE best practice (Neodyme, OpenRazer): keep golden logs committed for reproducibility,
document the protocol as field tables, and keep Lua dissectors with the `.pcapng` captures
in `../../captures/`.

Much of this is inherited from upstream rather than discovered from scratch — record here
only what we **confirm on this unit** or what **differs** from upstream.

## Suggested files (create as you go)

- `init-sequence.md` — the handshake step-by-step (PSK, GTLS, config upload, FDT calib).
- `golden-init.log` — a known-good captured init from this machine.
- `deltas.md` — anything that differs from upstream `driver_55x4.py` on our firmware.
