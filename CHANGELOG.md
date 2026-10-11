# Changelog

All notable changes to Funnel (Linux codename `gnu-sorter`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with pre-release channels: `indev` → `alpha` → `beta` → `rc` → stable
(see [`RELEASING.md`](RELEASING.md)).

## [Unreleased] — 3.0.0 "Tunneler"

### Added
- **Six-step rule wizard**: what to tidy → what to catch (real-file chips +
  type-ahead extension picker) → where it goes (folder picker) → fine-tune →
  priority & scope → review with a plain-English sentence and a custom
  "files flow into the folder" animation.
- **Rules page overhaul**: plain-English cards, priority numbers, per-rule
  enable toggle, edit/duplicate/reorder, a live filename tester, and an
  **"N sorted"** badge sourced from the journal index.
- **Journal search / index / backup**: in-memory index (by rule, folder, batch,
  time), filename/glob + date-range search, restore **preview**, backup and
  journal/rules **export & import**.
- **Journal filters**: `--journal-ext` (comma list) and `--journal-min-size` /
  `--journal-max-size` (with `K`/`M`/`G` suffixes), plus a file-type filter in
  the Activity view.
- **Restore conflict strategies**: `--on-conflict rename|overwrite|skip`,
  also selectable in the Restore dialog.
- **Configurable history retention**: `history_max_entries` and
  `history_max_bytes` (Settings → History), used when the journal trims itself.
- **Appearance**: pick System / Light / Dark in Settings (remembered across
  launches), and give any rule an optional **accent colour** (rules page shows a
  matching dot).
- **Flatpak (Linux)**: a GNOME-runtime manifest plus AppStream metadata, built
  by CI into a self-hosted OSTree repository on GitHub Pages
  (`…/gnu-sorter/flatpak/`). AppImage was evaluated and dropped in favour of a
  single, more portable Linux package.
- **Channel-aware, packaging-aware updates**: the update check only ever offers
  a build from the running channel (`indev`/`alpha`/`beta`/`rc`/`release`). On
  a Flatpak install it points at `flatpak update` instead of self-replacing,
  and a new **Check for updates automatically** switch (Settings → Updates)
  lets you opt out.
- `tools/release.py`, `RELEASING.md` and a CHANGELOG for a predictable
  `indev → alpha → beta → rc → release` cycle.

### Changed
- New per-rule **`match_mode`** (`any` / `all`, default `any`) with a
  "Require every condition" switch; existing rules are unaffected.
- Advanced editor: extensions are added via a dropdown + type-ahead instead of a
  comma-separated string; grouped fields; live plain-English preview.
- Version is now single-sourced from `gnu_sorter.__version__` (read dynamically
  by `pyproject.toml`).

### Fixed
- The system tray now finds a committed `data/icons/gnu-sorter.png`; the
  installer ships a committed hicolor PNG ladder (no rsvg/inkscape needed) and
  reuses the single-source `data/gnu-sorter.desktop`.


## [2.5.0] - 2026-10-11

### Added
- Auto-update support (checks GitHub Releases on launch and from the menu).
- Journal v2 with selectors, collision-safe restore, rotation and repair.

[Unreleased]: https://github.com/notmicrosoft2000-cmd/gnu-sorter/compare/v2.5.0...HEAD
[2.5.0]: https://github.com/notmicrosoft2000-cmd/gnu-sorter/releases/tag/v2.5.0
