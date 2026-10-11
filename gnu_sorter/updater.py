"""Self-update support for the Linux app.

The Linux build is distributed as GitHub Releases source tarballs (GitHub
generates these automatically for a tagged release). An update therefore is:

  1. look up the latest release via the GitHub API,
  2. download its tarball to a temp dir,
  3. copy the files over the install directory
     (~/.local/share/gnu-sorter/app for install.sh installs).

The running process keeps the already-imported modules in memory, so the
updated files take effect on the next launch (/ restart of the watcher).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
from pathlib import Path

from . import __version__

GITHUB_OWNER = "notmicrosoft2000-cmd"
GITHUB_REPO = "gnu-sorter"
LATEST_API = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest")

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


def latest_release() -> dict | None:
    """The newest release object from the GitHub API, or None (offline/404)."""
    return _http_json(LATEST_API)


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


def check() -> tuple[str, str | None, bool]:
    """Return (current, latest-tag-or-None, needs-update)."""
    rel = latest_release()
    if not rel:
        return __version__, None, False
    latest = rel.get("tag_name") or ""
    return (__version__, latest,
            bool(latest) and version_tuple(latest) > version_tuple(__version__))


# -- install layout -----------------------------------------------------

def install_dir() -> Path | None:
    """The app directory this module ships in, or None for non-install.sh uses.

    The module lives in ``<install>/gnu_sorter/``, so the install directory is
    this file's parent's parent. A git checkout also matches, which is handy
    for testing the update path.
    """
    here = Path(__file__).resolve().parent.parent
    if (here / "gnu_sorter").is_dir() and (here / "cli.py").is_file():
        return here
    return None


# -- update -------------------------------------------------------------

def update(dry_run: bool = False) -> tuple[bool, str]:
    """Download and install the newest release. Returns (ok, message)."""
    rel = latest_release()
    if not rel:
        return False, "could not check for updates (offline or no release yet)"
    tag = rel.get("tag_name") or ""
    if not tag or version_tuple(tag) <= version_tuple(__version__):
        return True, f"already up to date ({__version__})."
    tarball = rel.get("tarball_url")
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