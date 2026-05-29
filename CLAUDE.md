# Context for Claude (read this first)

This repo was scaffolded from a brainstorming session. It contains **no implementation
code yet** — only the design and context needed to start work. Your job in a new session
is to begin executing the design, starting at **Milestone 0**.

## What we're building

A Linux driver for the **Goodix `27c6:55a4`** fingerprint reader, so it can be used for
login / `sudo`. Full detail in
[`docs/superpowers/specs/2026-05-29-goodix-55a4-driver-design.md`](docs/superpowers/specs/2026-05-29-goodix-55a4-driver-design.md) —
**read it before doing anything.**

## Decisions already made (don't re-litigate without reason)

- **Driver-first.** Goal is *this* reader working end-to-end. Reusable AI/RE tooling is
  harvested opportunistically, not the primary goal.
- **Finish line = full fingerprint login** (fprintd + PAM), built incrementally.
- **Architecture = Hybrid (option C):** prototype + tune recognition in **Python** (fast
  loop), then port the proven approach into the **C libfprint driver** for the final
  PAM integration.
- **Build on the community work, via forks → PRs.** Don't start from scratch; don't
  reverse the Windows driver — the `55x4` protocol is already done upstream.
- **Workspace-repo + forked submodules** structure (this repo is the workspace).

## The single most important early question

The sensor only returns a **raw image**. libfprint *image drivers* hand the image to
libfprint's **built-in NBIS/Bozorth3 matcher** (the `bz3_threshold` field) — so we may
not need to write a matcher at all. BUT Goodix sensors are small/noisy, which is why the
goodixtls community went to a custom SIFT+CLAHE matcher instead.

**v0 must answer early: does NBIS find usable minutiae in our captured images?**
- Yes → tiny v0, libfprint+fprintd do the rest for free.
- No → fallback to preprocessing or custom matching (bigger v0).

Don't build the matcher until this is tested. See Milestone 1.

## Hard constraints

- **Biometric data never gets committed.** `data/` and `captures/` are gitignored.
  Captured fingerprint images and enrolled templates stay local. Double-check before any
  `git add -A`.
- **Password stays as a PAM fallback.** Fingerprint is `sufficient`, never the sole
  factor. This is match-on-host with a known PSK → it's a convenience factor, not a
  security boundary.
- **No PAM changes until FAR/FRR on a held-out set look sane** (see `eval/`).

## Device facts (verified 2026-05-29 on this machine)

- Reader: `Bus 001 Device 004: ID 27c6:55a4 Shenzhen Goodix ... FingerPrint Device`
- OS: Arch Linux, Wayland/KDE Plasma, user `g` (in `wheel`).
- Already installed: `fprintd` 1.94.5, `libfprint` 1.94.10. Upstream libfprint does
  **not** list `55a4` (confirmed: not in `/usr/lib/udev/rules.d/70-libfprint-2.rules`).
- No enrolled prints; `pam_fprintd` not configured anywhere yet.
- Tooling present: `python3` (3.14), `uv`, `git`, `gcc`/`clang`.
- **Not yet installed (will be needed):** `usbutils`, `wireshark-cli`/`tshark`,
  `meson`, `ninja`, `pyusb` (use a `uv` venv). See `research/README.md`.

## Next action

1. Set up `vendor/` per [`vendor/README.md`](vendor/README.md).
2. Start **Milestone 0** per [`research/README.md`](research/README.md): pull one raw
   image off the sensor with `goodix-fp-dump`'s `run_55a4.py`.

## If you use local LLMs for any AI-RE tooling

Point them at the ollama server on host **`steel`**, not localhost (per user memory).
