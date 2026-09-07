#!/usr/bin/env python3
"""Single source of truth for repo layout and where biometric data lives.

Captured frames and enrolled templates NEVER live in this repo (see CLAUDE.md).
They live in an out-of-repo *vault*, resolved in this order:

  1. ``$GOODIX_VAULT``
  2. ``<repo>/.vault-path`` -- a one-line, gitignored file holding the path.
     This is how the owner points at a personal location without that location
     ever entering the repository.
  3. ``~/.local/share/goodix-55a4`` (fallback; usually blocked by the guard below)

Why the mount guard: the vault is expected to sit on removable/external storage
that travels between dev machines. When that volume is not mounted, its
mountpoint is an ordinary empty directory on the internal disk, and a plain
``os.makedirs()`` will happily write biometric data there -- then silently shadow
it the moment the real volume is mounted over the top. That is not theoretical:
it is how a vault came to be stranded on an internal disk and was only found
months later. ``require_external()`` refuses to hand out a path whose nearest
mountpoint is ``/``.

Set ``GOODIX_VAULT_ALLOW_ROOT=1`` to override deliberately (e.g. a machine with
no separate data volume).
"""
import os
import pwd
import sys

ENV_VAR = "GOODIX_VAULT"
ALLOW_ROOT_VAR = "GOODIX_VAULT_ALLOW_ROOT"
LOCAL_PATH_FILE = ".vault-path"
FALLBACK_REL = (".local", "share", "goodix-55a4")


def repo_root() -> str:
    """Repo checkout root, derived from this file -- never hardcoded.

    Works under sudo, unlike expanduser("~"), and lets the checkout live
    anywhere.
    """
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def vendor(name: str = "goodix-fp-dump") -> str:
    """Path to a vendored upstream under <repo>/vendor/."""
    return os.path.join(repo_root(), "vendor", name)


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


def _local_path_file() -> str | None:
    """Path configured in the gitignored <repo>/.vault-path, if present."""
    try:
        with open(os.path.join(repo_root(), LOCAL_PATH_FILE)) as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    return line
    except OSError:
        pass
    return None


def root() -> str:
    """Vault root, before any mount check."""
    for candidate in (os.environ.get(ENV_VAR), _local_path_file()):
        if candidate:
            return os.path.abspath(os.path.expanduser(candidate))
    return os.path.join(user_home(), *FALLBACK_REL)


def _mount_point(path: str) -> str:
    """Nearest existing mountpoint at or above `path`."""
    p = os.path.realpath(path)
    while p != "/" and not os.path.ismount(p):
        p = os.path.dirname(p)
    return p


def require_external(path: str | None = None) -> str:
    """Return the vault root, or exit if it is not on a mounted data volume."""
    target = path or root()
    if os.environ.get(ALLOW_ROOT_VAR):
        return target
    if _mount_point(target) == "/":
        sys.exit(
            f"refusing to use vault: {target}\n"
            "It sits on the root filesystem, so either the data volume is not "
            "mounted or no vault is configured. Writing here would leave "
            "biometric data on the internal disk (and it would be shadowed once "
            "the real volume is mounted).\n"
            f"Mount the volume, set {ENV_VAR}, or write the path into "
            f"{LOCAL_PATH_FILE} (gitignored). {ALLOW_ROOT_VAR}=1 overrides."
        )
    return target


def frames() -> str:
    """The frames/ dir inside the guarded vault."""
    return os.path.join(require_external(), "frames")
