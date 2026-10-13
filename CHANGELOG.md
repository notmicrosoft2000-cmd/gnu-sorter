# Changelog

All notable changes to Funnel (Linux codename `gnu-sorter`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with pre-release channels: `indev` → `alpha` → `beta` → `rc` → stable
(see [`RELEASING.md`](RELEASING.md)).

## [Unreleased]

### Added
- **Green brand & accent system**: the app now wears Funnel's colour, taken
  from the project website (`#a3e635` on dark, `#4d7c0f` on light), applied to
  buttons, switches, selections and focus rings via an app stylesheet
  (`gnu_sorter/brand.css`).
- **Preferences → Accent colour**: follow the desktop, or pick a Funnel colour
  (green, lime, teal, cyan, blue, purple, pink, red, orange, yellow, slate).
  Defaults to **Funnel green** and re-tints automatically when the desktop
  switches between light and dark.
- **Empty states**: "No folders yet" and "Nothing sorted yet" now show a
  brand-tinted, gently floating illustration with an inline primary action.
- **Keyboard shortcuts dialog** (`Ctrl+?`), plus `Ctrl+N` for Sort Now.
- A busy spinner and "Sorting…" label on the Sort Now button during a sweep.
- AppStream `<branding>` colours, so software centres tint Funnel green.

### Fixed
- The wizard's **"Never match names matching"** field is now validated: an
  invalid exclusion regex is rejected up front instead of being saved and then
  silently ignored by the engine.
- **"Sort Now"** can no longer run two sweeps at once when triggered from the
  menu, the `Ctrl+N` shortcut or the tray while a sweep is already running.

### Changed
- **Rebuilt the app shell**: the hand-rolled sidebar is gone. Folders, Rules
  and Activity are now header tabs (`Adw.ViewSwitcher`), collapsing to a bottom
  switcher bar on narrow windows, over an animated view stack; Settings moved
  into a proper **Preferences dialog** (`Ctrl+,`). Content is centred in an
  `Adw.Clamp`, and page changes animate.
- **App icon is now green** to match the brand, regenerated (SVG, every PNG
  size, the ICO and the website images) from `data/icons/gnu-sorter.svg`.
- The wizard's "files flow into the folder" animation now flows into a green
  folder.

## [3.0.0] - 2026-10-12 "Tunneler"

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

[Unreleased]: https://github.com/notmicrosoft2000-cmd/gnu-sorter/compare/v3.0.0...HEAD
[3.0.0]: https://github.com/notmicrosoft2000-cmd/gnu-sorter/releases/tag/v3.0.0
[2.5.0]: https://github.com/notmicrosoft2000-cmd/gnu-sorter/releases/tag/v2.5.0
