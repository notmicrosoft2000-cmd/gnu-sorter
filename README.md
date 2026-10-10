# GNU Sorter v2.0

**Automatically sort any folder(s) with custom rules.** A GTK4 + libadwaita desktop app for Linux. Each watched folder can have its own independent ruleset; there's also a shared default template.

## Highlights

- **Per-folder rules** — keep different sorting rules for Downloads, Projects, Archives, etc.
- **Simple but powerful UI** — split view (folders · rules), **step-by-step wizard**, and a compact **Advanced editor**.
- **Quick presets** — GNOME defaults · Photography · Code projects · Empty.
- **GNOME-native** — uses Adw and modern GNOME patterns; clean, polished icon.
- **KDE/Dolphin service menu** — right-click "Sort with GNU Sorter" in Dolphin.
- **Safe** — skips `.part/.crdownload/.tmp/.partial`, waits for files to stabilise, collision-safe (`file (1).ext`), journaled `--undo`.
- **CLI + background watcher** — `--sweep`, `--watch`, `--add-rule [--root PATH]`, `--detach-rules/--attach-rules`.
- **User-local install** — `./install.sh` (no sudo). `./uninstall.sh` (optionally `--purge`).

## Installation (user-local)

1. **Clone**: `git clone https://github.com/notmicrosoft2000-cmd/gnu-sorter.git && cd gnu-sorter`
2. **Install**: `./install.sh` (add `--service` to enable the background watcher on login)
3. **Run**: `gnu-sorter --gui` (or launch from your applications menu)

**Uninstall**: `./uninstall.sh` — config and history are kept by default. Use `./uninstall.sh --purge` to remove `~/.config/gnu-sorter/` and `~/.local/share/gnu-sorter/history.log` too.

The installer copies the app to `~/.local/share/gnu-sorter/app/` and symlinks `~/.local/bin/gnu-sorter` there, so you can delete the clone after installation if you want.

## Quick start (GUI)

1. **Add folders** — `Folders → Add Folder` (e.g. `~/Downloads`, `~/Documents/Inbox`).
2. **Add a rule** — `Rules → Add Rule` opens a short wizard (pick extension or name → choose destination → review). The folder will get its own rules automatically.
3. **Go advanced** — `Rules ⋯ → Advanced rule editor…` for multi-extensions, name patterns, or regex.
4. **Presets** — `Rules ⋯ → Apply preset` to jump-start a common setup.
5. **Sort now** — button in the header, or right-click anywhere for context menu (About / Sort now).

## Quick start (CLI)

```bash
# List rules (default template)
gnu-sorter --list-rules

# Add a rule to Downloads only (auto-detaches Downloads if it inherits)
gnu-sorter --add-rule quant --folder quant --root ~/Downloads

# Show Downloads' effective rules + whether it's custom/inherited
gnu-sorter --list-rules --root ~/Downloads

# Revert Downloads to the shared template
gnu-sorter --attach-rules --root ~/Downloads

# Sweep once, watch in background, undo last move
gnu-sorter --sweep
gnu-sorter --watch
gnu-sorter --undo-last 1
```

## Migration

Upgrading from `download-sorter` (v1): `gnu-sorter --finish-migration` retires the old user service and takes over. Existing roots, rules, and history are ported. On first run with v2, the current global rules become the default template; folders continue to behave as before until you customise them.

## Architecture (at a glance)

- `gnu_sorter/config.py` – template `rules.json`, per-folder `roots.d/<sha256>.json`, roots, settings; normalised paths.
- `gnu_sorter/engine.py` – resolves effective rules per root (template or custom), compiles and caches by mtime, skips in-progress files, collision-safe moves, journal to `~/.local/share/gnu-sorter/history.log`.
- `gnu_sorter/gui.py` – GTK4/Adw UI (split view, wizard, advanced dialog, presets, search, About, context menu, hamburger).
- `gnu_sorter/watcher.py` – single `inotifywait` across all roots with stability window; polling fallback.
- `gnu_sorter/service.py` – systemd user unit helpers.
- `gnu_sorter/cli.py` – `--root`-aware rules commands, sweep/watch/undo/roots/migration.

## Requirements

- Linux (Wayland/X11)
- Python 3.11+
- GTK 4.24+, libadwaita 1.6+ (tested on 1.10)
- `inotifywait` (recommended) for low-latency watching; falls back to polling.
- `rsync`/`curl`/`rsvg-convert` optional (installer uses them if present)

## About

- Creator: **notmicrosoft2000-cmd**
- Version: **2.0.0**
- Website: https://notmicrosoft2000-cmd.github.io/gnu-sorter/
- Repository: https://github.com/notmicrosoft2000-cmd/gnu-sorter
- Issues: https://github.com/notmicrosoft2000-cmd/gnu-sorter/issues
- License: **GPL-3.0**

## Contributing / Development

```bash
cd ~/Documents/Projects/gnu-sorter
python3 -m py_compile main.py gnu_sorter/*.py
# GUI smoke test (optional, needs display)
export GS_SMOKE_OUT=/tmp/opencode/gsui && python3 tests/smoke_gui.py
```

## Acknowledgements

Built on GTK 4 and libadwaita. Inspired by keeping organisation simple without sacrificing power.
EOF
echo "ok"
