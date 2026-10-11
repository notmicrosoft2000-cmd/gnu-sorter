"""Self-update support for the Linux app.

Two install kinds are supported. A **script** install (``install.sh`` /
``~/.local/share/gnu-sorter``) updates by downloading the release tarball and
syncing it over the install directory. A **Flatpak** install is read-only, so
there we only report that an update exists and point at ``flatpak update``.

Updates are **channel-aware**: a copy on ``indev`` is only ever offered
``indev`` builds, one on ``release`` only stable ones. The channel is derived
from ``__version__`` (PEP 440): ``.devN`` → indev, ``aN`` → alpha, ``bN`` →
beta, ``rcN`` → rc, none → release.

The running process keeps the already-imported modules in memory, so the
updated files take effect on the next launch (/ restart of the watcher).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from . import __version__

GITHUB_OWNER = "notmicrosoft2000-cmd"
GITHUB_REPO = "gnu-sorter"
RELEASES_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases")
LATEST_API = f"{RELEASES_API}/latest"
FLATPAK_ID = "io.neptunepc.Funnel"

_USER_AGENT = f"gnu-sorter/{__version__}"


# -- HTTP ---------------------------------------------------------------

def _http_json(url: str, timeout: float = 15) -> dict | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
        })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except Exception:
        return None


def _download(url: str, dest: Path, timeout: float = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, \
            open(dest, "wb") as fh:
        shutil.copyfileobj(resp, fh)


# -- versions -----------------------------------------------------------

def version_tuple(v: str) -> tuple[int, int, int]:
    """Parse 'v2.5.0'/'2.5.0-rc1' into (2, 5, 0); (0, 0, 0) when it can't."""
    parts: list[int] = []
    for chunk in v.strip().lstrip("vV").replace("-", ".").split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
    return tuple((parts + [0, 0, 0])[:3])


# Phase ordering within one release number (PEP 440): dev < a < b < rc < final.
_PHASE_RANK = {"dev": 0, "a": 1, "b": 2, "rc": 3, "final": 4}
_PHASE_CHANNEL = {"dev": "indev", "a": "alpha", "b": "beta",
                  "rc": "rc", "final": "release"}


def _normalize(v: str) -> str:
    """Fold a tag (``v3.0.0-rc.1``) into one PEP-440-ish spelling (``3.0.0rc1``)."""
    s = (v or "").strip().lstrip("vV").lower()
    s = s.replace("alpha", "a").replace("beta", "b").replace("indev", "dev")
    return re.sub(r"[-._]?(a|b|rc|dev)\.?(\d*)", r"\1\2", s)


def _parse(v: str) -> tuple[tuple[int, ...], str, int]:
    n = _normalize(v)
    m = re.match(r"(\d+(?:\.\d+)*)(.*)", n)
    release = tuple(int(x) for x in m.group(1).split(".")) if m else (0,)
    rest = m.group(2) if m else ""
    pm = re.match(r"\.?dev(\d*)", rest)
    if pm:
        phase, num = "dev", int(pm.group(1) or 0)
    else:
        pm = re.match(r"\.?(a|b|rc)(\d*)", rest)
        phase, num = (pm.group(1), int(pm.group(2) or 0)) if pm else ("final", 0)
    rel = tuple(release) + (0,) * max(0, 3 - len(release))
    return rel, phase, num


def version_key(v: str) -> tuple:
    """A comparable key across channels (PEP 440 ordering)."""
    rel, phase, num = _parse(v)
    return rel, _PHASE_RANK[phase], num


def channel_of(v: str) -> str:
    """'indev' | 'alpha' | 'beta' | 'rc' | 'release' for a version or tag."""
    return _PHASE_CHANNEL[_parse(v)[1]]


def install_kind() -> str:
    """How this copy is installed: 'flatpak', 'script' or 'unknown'."""
    if os.environ.get("FLATPAK_ID") or Path("/.flatpak-info").is_file():
        return "flatpak"
    if install_dir() is not None:
        return "script"
    return "unknown"


# -- releases -----------------------------------------------------------

def _releases() -> list[dict]:
    data = _http_json(RELEASES_API + "?per_page=30")
    return [r for r in data if isinstance(r, dict)] if isinstance(data, list) \
        else []


def latest_release() -> dict | None:
    """The newest release object from the GitHub API, or None (offline/404)."""
    return _http_json(LATEST_API)


def _best_release() -> dict | None:
    """The newest release whose channel matches the running version."""
    channel = channel_of(__version__)
    best: dict | None = None
    for rel in _releases():
        if rel.get("draft"):
            continue
        tag = rel.get("tag_name") or ""
        if not tag or channel_of(tag) != channel:
            continue
        if best is None or version_key(tag) > version_key(best.get("tag_name", "")):
            best = rel
    return best


def check() -> tuple[str, str | None, bool]:
    """Return (current, best-tag-in-this-channel-or-None, needs-update).

    Channels are exclusive: a copy on ``indev`` is only offered ``indev``
    builds, and the comparison happens *within* the channel.
    """
    best = _best_release()
    tag = (best or {}).get("tag_name") or None
    needed = bool(tag) and version_key(tag) > version_key(__version__)
    return __version__, tag, needed


# -- install layout -----------------------------------------------------

def install_dir() -> Path | None:
    """The app directory this module ships in, or None for non-install.sh uses.

    The module lives in ``<install>/gnu_sorter/``, so the install directory is
    this file's parent's parent. Both an ``install.sh`` copy and a git checkout
    have ``main.py`` + ``gnu_sorter/cli.py`` there; a pip install has neither.
    """
    here = Path(__file__).resolve().parent.parent
    if (here / "main.py").is_file() and (here / "gnu_sorter" / "cli.py").is_file():
        return here
    return None


# -- update -------------------------------------------------------------

def update(dry_run: bool = False) -> tuple[bool, str]:
    """Download and install the newest release in this channel.

    Returns ``(ok, message)``. A Flatpak install is read-only, so it never
    self-replaces: it reports the available version and the command to run.
    """
    best = _best_release()
    if best is None:
        return False, "could not check for updates (offline or no release yet)"
    tag = best.get("tag_name") or ""
    if not tag or version_key(tag) <= version_key(__version__):
        return True, f"already up to date ({__version__})."

    if install_kind() == "flatpak":
        return (False, f"{tag} is available — update the Flatpak with "
                       f"`flatpak update {FLATPAK_ID}`.")

    tarball = best.get("tarball_url")
    if not tarball:
        return False, f"release {tag} has no source tarball."
    target = install_dir()
    if target is None:
        return (False, "cannot locate the install directory — this copy was "
                       "not installed via install.sh (pip-installed?).")

    if dry_run:
        return True, f"update available: {__version__} → {tag}"

    print(f"[update] {__version__} → {tag}")
    print(f"[update] downloading {tarball} …")
    with tempfile.TemporaryDirectory(prefix="gnu-sorter-update-") as td:
        tmp = Path(td)
        archive = tmp / f"{tag.replace('/', '_')}.tar.gz"
        _download(tarball, archive)
        extract = tmp / "src"
        extract.mkdir()
        shutil.unpack_archive(str(archive), extract)
        # GitHub's source tarballs unpack to a single directory whose name is
        # "<owner>-<repo>-<sha>/" (not the tag), so take the one directory.
        dirs = [p for p in extract.iterdir() if p.is_dir()]
        if len(dirs) != 1:
            return (False, f"unexpected release layout: "
                           f"{[d.name for d in dirs]}.")
        src = dirs[0]
        _sync(src, target)

    _ensure_executable(target / "main.py")
    return True, f"updated to {tag} — restart the app / watcher to use it."


def _sync(src: Path, dst: Path) -> None:
    """Copy ``src`` tree into ``dst``, merging and dropping stale bytecode.

    Nothing user-generated lives in the install directory (config lives under
    ~/.config), so top-level files removed in the new release are deleted.
    """
    dst.mkdir(parents=True, exist_ok=True)
    # Remove top-level entries that no longer exist in the new release.
    wanted = {e.name for e in src.iterdir()}
    for name in {p.name for p in dst.iterdir()} - wanted:
        stale = dst / name
        if stale.is_dir() and not stale.is_symlink():
            shutil.rmtree(stale, ignore_errors=True)
        else:
            stale.unlink(missing_ok=True)
    for entry in src.iterdir():
        if entry.name in (".git", ".github", "dist", "build", "__pycache__") \
                or entry.name.endswith(".pyc"):
            continue
        target = dst / entry.name
        if entry.is_dir():
            shutil.copytree(
                entry, target, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", ".git", ".github", "dist", "build"))
        else:
            shutil.copy2(entry, target)
    for pycache in list(dst.rglob("__pycache__")):
        shutil.rmtree(pycache, ignore_errors=True)


def _ensure_executable(path: Path) -> None:
    if path.is_file():
        path.chmod(path.stat().st_mode | 0o111)