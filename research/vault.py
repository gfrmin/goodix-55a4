#!/usr/bin/env python3
"""Single source of truth for where biometric data lives.

Captured frames and enrolled templates NEVER live in this repo (see CLAUDE.md).
They live on the portable `yo` volume, under:

    $GOODIX_VAULT/

Why the mount guard: `yo` is an *external* disk that travels between dev
machines. When it is not mounted, `$GOODIX_VAULT` resolves to an empty mountpoint
directory on the internal root disk, and a plain `os.makedirs()` will happily
write biometric frames there instead -- then silently shadow them the moment the
real volume is mounted over the top. That is exactly how a stray vault came to
sit in an unrelated directory in the developer's home (found and rescued 2026-08-01). `require_external()`
refuses to hand out a path that is not on a real non-root mount.

On another machine, point GOODIX_VAULT at the vault instead of relying on `$GOODIX_VAULT`.
Set GOODIX_VAULT_ALLOW_ROOT=1 only if you deliberately want it on the root disk.
"""
import os
import pwd
import sys

ENV_VAR = "GOODIX_VAULT"
ALLOW_ROOT_VAR = "GOODIX_VAULT_ALLOW_ROOT"
_REL = "yo/data/personal/goodix-55a4"


def user_home() -> str:
    """Home of the *invoking* user, not root.

    The capture scripts run under sudo for USB access, where
    os.path.expanduser("~") is /root -- which would dump biometric frames into
    root's home instead of the user vault.
    """
    who = os.environ.get("SUDO_USER")
    if who:
        return pwd.getpwnam(who).pw_dir
    return os.path.expanduser("~")


def root() -> str:
    """Vault root, before any mount check."""
    override = os.environ.get(ENV_VAR)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.join(user_home(), _REL)


def _mount_point(path: str) -> str:
    """Nearest existing mountpoint at or above `path`."""
    p = os.path.realpath(path)
    while p != "/" and not os.path.ismount(p):
        p = os.path.dirname(p)
    return p


def require_external(path: str | None = None) -> str:
    """Return the vault root, or exit if the `yo` volume isn't mounted."""
    target = path or root()
    if os.environ.get(ALLOW_ROOT_VAR):
        return target
    if _mount_point(target) == "/":
        sys.exit(
            f"refusing to use vault: {target}\n"
            "It sits on the root disk -- the external `yo` volume is not mounted "
            "there, so writing would leave biometric data on the internal disk "
            "(and it would be shadowed once `yo` is mounted).\n"
            f"Mount `yo`, or set {ENV_VAR} to the vault path on this machine "
            f"(or {ALLOW_ROOT_VAR}=1 to override deliberately)."
        )
    return target


def frames() -> str:
    """The frames/ dir inside the guarded vault."""
    return os.path.join(require_external(), "frames")
