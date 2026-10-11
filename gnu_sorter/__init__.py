"""Funnel — sort any folder automatically."""

# Single source of truth for the version. `pyproject.toml` reads this via a
# dynamic `attr`, and `tools/release.py` bumps this one line. The channel is
# derived from the suffix (PEP 440): `.devN`→indev, `aN`→alpha, `bN`→beta,
# `rcN`→rc, none→release.
__version__ = "3.0.0.dev0"
APP_NAME = "Funnel"
APP_ID = "io.neptunepc.Funnel"
CREATOR = "notmicrosoft2000-cmd"
WEBSITE = "https://notmicrosoft2000-cmd.github.io/gnu-sorter/"
REPO = "https://github.com/notmicrosoft2000-cmd/gnu-sorter"
ISSUES = f"{REPO}/issues"

# Brand accent — the lime used across the website and the app chrome.
BRAND = "#a3e635"
BRAND_FG = "#0b0d0a"
