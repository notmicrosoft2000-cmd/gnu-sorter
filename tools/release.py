#!/usr/bin/env python3
"""Release helper for Funnel (Linux, codename ``gnu-sorter``).

Encodes the release cycle described in ``RELEASING.md`` so every release is cut
the same way. The version lives in exactly one place —
``gnu_sorter/__init__.py`` — and this tool is the only thing that edits it.

Channels (PEP 440 suffix on the left, human label on the right):

    X.Y.Z.devN   ->  indev     built from every push to main
    X.Y.ZaN      ->  alpha     tagged vX.Y.Z-alpha.N
    X.Y.ZbN      ->  beta      tagged vX.Y.Z-beta.N
    X.Y.ZrcN     ->  rc        tagged vX.Y.Z-rc.N
    X.Y.Z        ->  release   tagged vX.Y.Z

Usage::

    tools/release.py current
    tools/release.py bump {major|minor|patch}     # new dev cycle on main
    tools/release.py cut {alpha|beta|rc|release}  # advance a channel

    # options
    --dry-run        print the result, write nothing
    --commit         commit the version bump
    --tag            create the git tag (implies --commit)
    --push           push the commit and tag (implies --commit --tag)
    --roll-changelog move the CHANGELOG "Unreleased" body under the new version
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "gnu_sorter" / "__init__.py"
CHANGELOG = ROOT / "CHANGELOG.md"

_VERSION_LINE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.M)
_CORE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+)|\.dev(\d+))?$")
_PRE_LABEL = {"a": "alpha", "b": "beta", "rc": "rc"}


# -- version model ------------------------------------------------------

def read_version() -> str:
    m = _VERSION_LINE.search(INIT.read_text())
    if not m:
        sys.exit(f"error: no __version__ line found in {INIT}")
    return m.group(1)


def write_version(version: str) -> None:
    text = INIT.read_text()
    new, n = _VERSION_LINE.subn(f'__version__ = "{version}"', text, count=1)
    if n != 1:
        sys.exit(f"error: could not rewrite __version__ in {INIT}")
    INIT.write_text(new)


def parse(version: str) -> tuple[int, int, int, str, int]:
    """'3.0.0a1' -> (3, 0, 0, 'alpha', 1); '3.0.0' -> (..., 'release', 0)."""
    m = _CORE.match(version.strip())
    if not m:
        sys.exit(f"error: unparseable version {version!r} (expected PEP 440)")
    major, minor, patch = int(m[1]), int(m[2]), int(m[3])
    if m[4]:
        return major, minor, patch, _PRE_LABEL[m[4]], int(m[5])
    if m[6] is not None:
        return major, minor, patch, "indev", int(m[6])
    return major, minor, patch, "release", 0


def render(major: int, minor: int, patch: int, channel: str, n: int) -> str:
    base = f"{major}.{minor}.{patch}"
    if channel == "indev":
        return f"{base}.dev{n}"
    if channel == "alpha":
        return f"{base}a{n}"
    if channel == "beta":
        return f"{base}b{n}"
    if channel == "rc":
        return f"{base}rc{n}"
    return base


def tag_name(version: str) -> str:
    major, minor, patch, channel, n = parse(version)
    base = f"v{major}.{minor}.{patch}"
    return base if channel == "release" else f"{base}-{channel}.{n}"


def bump(version: str, part: str) -> str:
    major, minor, patch, _channel, _n = parse(version)
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return render(major, minor, patch, "indev", 0)


def cut(version: str, channel: str) -> str:
    major, minor, patch, current, n = parse(version)
    if channel == "release":
        return render(major, minor, patch, "release", 0)
    n = n + 1 if current == channel else 1
    return render(major, minor, patch, channel, n)


# -- changelog ----------------------------------------------------------

def roll_changelog(version: str, today: str) -> bool:
    """Move the ``## [Unreleased]`` body under a new ``## [tag] - date``.

    Leaves a fresh empty Unreleased section on top. No-op when the changelog or
    the Unreleased heading is missing.
    """
    if not CHANGELOG.exists():
        return False
    text = CHANGELOG.read_text()
    head = "## [Unreleased]"
    start = text.find(head)
    if start == -1:
        return False
    after = text.find("\n## [", start + len(head))
    if after == -1:
        sys.exit("error: CHANGELOG Unreleased section has no following version")
    body = text[start + len(head):after].lstrip("\n")
    tag = tag_name(version)
    new = (text[:start]
           + f"{head}\n\n## [{tag}] - {today}\n\n{body}"
           + text[after:])
    CHANGELOG.write_text(new)
    return True


# -- git ----------------------------------------------------------------

def _git(*args: str) -> None:
    subprocess.run(["git", "-C", str(ROOT), *args], check=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cut a Funnel release.")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("current", help="show the current version and channel")

    p_bump = sub.add_parser("bump", help="start a new dev cycle on main")
    p_bump.add_argument("part", choices=["major", "minor", "patch"])

    p_cut = sub.add_parser("cut", help="advance a release channel")
    p_cut.add_argument("channel", choices=["alpha", "beta", "rc", "release"])

    for p in (p_bump, p_cut):
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--commit", action="store_true")
        p.add_argument("--tag", action="store_true")
        p.add_argument("--push", action="store_true")
        p.add_argument("--roll-changelog", action="store_true")

    args = ap.parse_args(argv)

    current = read_version()

    if args.command == "current":
        channel = parse(current)[3]
        print(f"version: {current}")
        print(f"channel: {channel}")
        print(f"tag:     {tag_name(current)}")
        return 0

    if args.command == "bump":
        new = bump(current, args.part)
    else:
        new = cut(current, args.channel)

    if new == current:
        print(f"already at {current}; nothing to do.")
        return 0

    print(f"{current}  ->  {new}   (tag {tag_name(new)})")
    if args.dry_run:
        return 0

    write_version(new)

    if args.command == "cut" and args.roll_changelog:
        today = _dt.date.today().isoformat()
        if roll_changelog(new, today):
            print(f"changelog: rolled [Unreleased] -> [{tag_name(new)}] - {today}")

    if args.commit or args.tag or args.push:
        _git("add", "gnu_sorter/__init__.py", "CHANGELOG.md")
        _git("commit", "-m", f"release: {tag_name(new)}")
    if args.tag or args.push:
        _git("tag", "-a", tag_name(new), "-m", f"Funnel {new}")
    if args.push:
        _git("push", "origin", "HEAD")
        if args.tag or args.push:
            _git("push", "origin", tag_name(new))

    print("done." if (args.commit or args.tag or args.push)
          else "written to the working tree (no --commit).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
