#!/bin/sh
# Flatpak entry point for Funnel.
#
# The application code is installed read-only under /app/share/gnu-sorter; the
# GNOME runtime already provides python3 + PyGObject (gi) + GTK4/libadwaita, so
# there is nothing to build here. Optional helpers (watchdog, pystray,
# platformdirs) are absent inside the sandbox and Funnel falls back cleanly.
set -eu
export PYTHONPATH="/app/share/gnu-sorter${PYTHONPATH:+:$PYTHONPATH}"
exec python3 /app/share/gnu-sorter/main.py "$@"
