# goodix-55a4

Writing a Linux driver for the **Goodix `27c6:55a4`** fingerprint reader so it can be
used for login / `sudo` via `fprintd` + PAM.

This reader has **no official Linux driver** and is **not** supported by upstream
`libfprint` or the proprietary TOD packages. The protocol for the `55x4` family has,
however, already been largely reverse-engineered by the community
([goodix-fp-linux-dev](https://github.com/goodix-fp-linux-dev)), so this project stands
on that work rather than starting from a Windows-driver teardown.

## Status

🟡 **Scaffold / pre-implementation.** Design is written; no code yet.
Start at **Milestone 0** (prove we can pull a raw image off *this* unit).

## The shape of the problem

The sensor returns a **raw capacitive image** — it does *not* say "match / no-match".
So a working driver is three jobs:

1. **Capture** — USB + GTLS (TLS-PSK) handshake → raw frames. *(mostly solved upstream for `55x4`)*
2. **Recognition** — turn an image into accept/reject. **The open question is whether
   libfprint's built-in NBIS/Bozorth3 matcher works on these small, noisy images, or
   whether we need custom matching (SIFT/CLAHE).** This is the first thing v0 must test.
3. **Integration** — deliver into `fprintd` → PAM, with password kept as a fallback.

## Quickstart

```sh
# 1. Read the context, then the design spec:
cat CLAUDE.md
cat docs/superpowers/specs/2026-05-29-goodix-55a4-driver-design.md

# 2. Set up the vendored upstreams (forks) — see vendor/README.md
# 3. Begin Milestone 0 — see research/README.md
```

## Layout

| Path | What |
|---|---|
| `CLAUDE.md` | Orientation for a new session — read this first |
| `docs/` | Design spec, protocol notes, the reusable RE "recipe" |
| `research/` | Python: capture, NBIS-on-image test, preprocessing, matcher fallback |
| `eval/` | Held-out test set + FAR/FRR scoring |
| `data/` | Raw images + templates — **biometric, hard-gitignored, never committed** |
| `captures/` | `.pcapng` + Lua dissectors |
| `vendor/` | Forked submodules: `goodix-fp-dump` (capture), `libfprint` (the C driver) |

## Security note

Match-on-host + a known PSK means this is a **convenience factor, not a security
boundary**. Keep your password working. See the threat-model section of the design spec.
