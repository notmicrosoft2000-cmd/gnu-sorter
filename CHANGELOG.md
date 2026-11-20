# Changelog

All notable changes to Funnel (Linux codename `gnu-sorter`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with pre-release channels: `indev` → `alpha` → `beta` → `rc` → stable
(see [`RELEASING.md`](RELEASING.md)).

## [Unreleased]

### Added
- **Context rail and three sections**: `01 Plan`, `02 Inputs` and `03 Log`. The
  rule sheet for the selected folder is the landing view. `00 Shared defaults`
  is a context of its own, so a folder either uses it or has an own copy.
- **Static proof panel**: the wizard's animated preview is gone. Counts and a
  compact list of sample rows are drawn once, in the same technical desk
  language as the rest of the app.
- **Install confirmation**: checking for an update only reports the result. The
  result sheet asks before installing anything, and the Flatpak path keeps its
  command-only guidance.
- **Green brand & accent system**: the app now wears Funnel's colour, taken
  from the project website (`#a3e635` on dark, `#466f0d` on light), applied to
  buttons, switches, selections and focus rings via an app stylesheet
  (`gnu_sorter/brand.css`).
- **Preferences → Accent colour**: follow the desktop, or pick a Funnel colour
  (green, lime, teal, cyan, blue, purple, pink, red, orange, yellow, slate).
  Defaults to **Green** and re-tints automatically when the desktop
  switches between light and dark.
- **Empty states**: "No folders yet" and "Nothing sorted yet" now show a
  brand-tinted illustration with an inline primary action.
- **Keyboard shortcuts dialog** (`Ctrl+?`), plus `Ctrl+N` for Sort Now.
- A busy spinner and "Sorting…" label on the Sort Now button during a sweep.
- AppStream `<branding>` colours, so software centres tint Funnel green.
- **Rules page redesign**: one unified, priority-ordered list (no more separate
  sections), with **drag-and-drop reordering** (and ▲▼ in each rule's `⋯` menu
  for the keyboard); the presets moved into the **Add Rule** split button, and
  the page's `⋯` menu now holds the advanced editor plus the new import/export.
- **Overlap warnings**: rules that can never match, such as a duplicated
  extension or anything below a catch-all, now raise a banner that names the
  rule and says why, instead of silently doing nothing.
- **Preview a folder** from the rules page: counts how many top-level files
  each rule would move, before you press Sort Now.
- **Rule import & export** (plain JSON) from the rules page, sharing the same
  format as the advanced editor; importing asks whether to *Replace* or
  *Append* (same-named rules are overwritten).

### Fixed
- **Sort Now** honours the saved Dry-run by default setting and names the
  effective mode in its result toast.
- **Undo** shows a preview with the affected count and first file before it
  moves anything. Undo actions stay disabled while a sweep is running, and the
  sweep guard now lives in one controller shared by the menu, shortcut and tray.
- **Destructive actions ask first**: resetting to defaults, removing a folder,
  replacing a rule sheet and removing a rule now name the target. Rule export
  writes through a temporary file instead of truncating the destination.
- The wizard's **"Never match names matching"** field is now validated: an
  invalid exclusion regex is rejected up front instead of being saved and then
  silently ignored by the engine.
- **"Sort Now"** can no longer run two sweeps at once when triggered from the
  menu, the `Ctrl+N` shortcut or the tray while a sweep is already running.
- Hand-painted accents, including the static proof and rule colour dots, follow
  the active accent and its light or dark variant instead of a fixed lime.

### Changed
- **Desk icons and cleaner labels**: the rail, sections, primary actions and
  rule controls now use symbolic icons from the active icon theme. Numeric
  indices and the `00` prefix are gone from the desk and the rule sheet.
- **GUI rebuilt from scratch**: `gnu_sorter/gui.py` is now a thin compatibility
  facade over the `gnu_sorter/gui_shell/` package. The header tabs are replaced
  by a context rail and three sections, and the six-step wizard is now a
  four-step draft: Catch, Destination, Conditions, Proof. Engine, config,
  watcher, journal and updater behaviour is unchanged.
- **App icon is now green** to match the brand, regenerated (SVG, every PNG
  size, the ICO and the website images) from `data/icons/gnu-sorter.svg`.
- **Visual restyle**: the app chrome now matches the project website's quiet
  technical-desk look. Hairline rules replace the soft accent glow on hover,
  small structural labels (section eyebrows, row indices, row metadata) are set
  in monospace, and hand-painted elements use tight corners. The empty-state
  illustration no longer drifts.
- **Project website rebuilt** on the same visual system, with self-hosted Fira
  Sans and copy that reads like a manual instead of a landing page.

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
