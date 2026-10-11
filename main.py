#!/usr/bin/env python3
"""Funnel entry point — works even when invoked through a symlink."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from gnu_sorter.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
