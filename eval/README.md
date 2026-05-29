# eval/ — recognition quality (Milestone 2)

Scoring harness for the recognition approach chosen in M1. **The gate before any PAM
changes:** the numbers here must be sane before fingerprint auth is trusted.

## What to build

- A held-out test set: multiple captures of your enrolled finger(s) + "impostor"
  captures (other fingers). Raw images live in `../data/` (gitignored) — `eval/` holds
  only code and reports, never images.
- Scoring that reports:
  - **FRR** (false reject rate) — enrolled finger wrongly rejected → usability.
  - **FAR** (false accept rate) — wrong finger wrongly accepted → security.
  - A threshold sweep (FRR/FAR tradeoff) to pick the operating point.

## Gate

Don't wire PAM (M4) until FAR is low at a usable FRR. Match-on-host means *this matcher's
FAR is the lock* — see the threat model in the design spec.
