# Funnel v3.0 — "Tunneler" spec

Working spec for the v3.0 release, codename **Tunneler**. Build order:
**Linux first, then Windows.** Both apps share the same feature set and
keep the branding-only rename (paths, commands, exe name, URLs unchanged).

Locked decisions (session with user, 2026-10-11):

- Ships **all four** themes: download detection, journal v2 & restore,
  new rule wizard, design/mascot.
- Mascot: **cute character** (a little funnel/tunnel creature with a face).
- Download detection default: **extra cautious** — when unsure, DON'T sort;
  park the file longer.
- Journal depth: **metadata only** — batch IDs + size/mtime verification +
  rotation + targeted restore. No per-file checksums (fast on huge folders).

---

## 1. Download detection — "don't sort it while it's still downloading"

**Today (v2.x):** skip files ending in a progress suffix (`.part`,
`.crdownload`, `.download`, `.tmp`, `.partial`), plus a fixed ~3 s
size-stability window. Weak spots: window too short for big files; paused-but-
stable files slip through; direct writers (yt-dlp, wget/curl -O, Steam, scripts)
have no suffix to catch.

**v3 — two-tier "hold" detection (Linux DONE, engine-core; Windows mirror
pending):**

- **Tier 1 — rename-aware park:** any file seen with a progress suffix is
  *held* and reported; it is never sorted. The browser/managers' rename-on-
  complete (`x.part` → `x.mp4`) is the definitive "done" signal. If the file
  keeps its suffix, it stays parked forever (extra-cautious default: paused
  downloads are never touched).
- **Tier 2 — growth-aware settle:** files without a suffix must pass a
  multi-probe stability check (size AND mtime constant across probes). Any
  growth or in-place rewrite resets the check.
- **Size-proportional settle window:** files > 100 MB get more settling time
  (+4 s per extra 100 MB, capped at +60 s). A 3 GB file must stay quiet for
  `base + 60 s` before it's trusted.
- **Held state is visible:** sweep stats now include `held`/`held_files`
  ("waiting (downloads / too fresh)") in the CLI summary and `--dry-run`
  lists exactly which files are parked. GUI Activity gets a "Waiting for
  download" list + settings toggle (Linux GUI toggle done).
- **Setting:** `cautious_downloads` (default `true`) — an "Extra-cautious
  download detection" switch in settings. When off, one probe instead of two.

Accepted trade-off: extra-cautious means a just-copied or freshly created file
may sit a beat longer before being sorted — that's the point.

### Linux (DONE — Phase 1)
- `engine.py`: `IN_PROGRESS_SUFFIXES` extended (`.opdownload`, `.ytdl`,
  `.!qb`, `.un~`, `.filepart`), `settle_window_seconds()`,
  `is_stable()` → 3 probes + size/mtime constancy + proportional window,
  `sweep()` → held tracking + 2-probe cautious confirm.
- `config.py`: `cautious_downloads: True` default.
- `cli.py`: summary shows `N waiting (downloads / too fresh)`; `--dry-run`
  lists held files.
- `gui.py`: "Extra-cautious download detection" switch.
- `tests/test_detection.py`: 7 tests (suffixes, settle window, probe checks,
  sweep holds in-progress / too-fresh / growing, sweep moves stable).

### Windows (DONE — Phase 1b)
- `SortEngine`: same extended suffixes, `SettleWindowSeconds`, multi-probe
  `IsStable` (size + mtime), two-probe cautious sweep, plus the Windows-only
  **open-handle check** (`IsFileBusy`: a sharing violation on our open-for-
  write attempt ⇒ another process is mid-write ⇒ file is held, not sorted).
- `WatchService` stability loop stands down while a handle is open (up to
  1 min).
- `SortStats` gains `Held`/`HeldFiles`; "Sort now" reports "N waiting
  (downloads / too fresh)".
- Same `cautious_downloads` setting (default true) with a Settings toggle
  (`AppSettings` + `SettingsViewModel` + `SettingsView.xaml`).
- C# has no local .NET toolchain — validated by the MSBuild CI workflow on
  every `main` push.

---

## 2. Journal v2 & restore — "time machine for your folders"

**Today (v2.x):** JSONL of every move; `--undo` reverses the last N; the
Activity tab lists past moves.

**v3 target:**

- **Batches:** every sweep gets an operation ID; undo restores the whole
  sweep atomically in reverse order (not one file).
- **Rich entries:** source, destination, size, mtime, rule name, root,
  timestamp, batch ID.
- **Targeted restore:** "restore everything moved in the last N hours", "all
  from [folder]", "files caught by [rule]", or select rows in Activity →
  Restore. All with a **dry-run preview** first.
- **Collision-safe:** if the original path is now taken, auto
  `name (1).ext` or prompt.
- **Integrity:** entries store size/mtime so restore skips already-handled
  rows; journal rotates (keep last N MB) and can be repaired via CLI.
- **Cross-platform:** same journal format both apps write.
- **CLI:** `funnel journal` (list/query), `funnel undo --since 2h`,
  `funnel restore --folder X --before 2026-10-11`, `funnel journal --repair`.
- **Killer UX:** Undo from the notification — "Moved 12 files to Documents"
  with an Undo action (libnotify actions on Linux, toast buttons on Windows).

### Linux (DONE — Phase 2)
- **Journal v2:** entries include `v:2`, `id`, `batch` (sweep op id), `rule`, `root`, `size`, `mtime`. Backwards-compatible with v1 JSON and legacy tab-separated lines.
- **Rich selectors:** `records()`/`undo()` accept `limit`, `since`/`before` (durations `30m,2h,7d` or timestamps `YYYY-MM-DD[ HH:MM:SS]`), `folder`, `rule` (case-insensitive).
- **Undo improvements:** collision-safe restore (if source occupied, restore to `name (1).ext`); already-restored detection via size+mtime (drops from journal); warns if size changed; keeps un-reversible rows on failure.
- **CLI:** `--journal` (newest-first listing; selectors + `--repair`), `--restore` (requires a selector).
- **Rotation:** trims to last ~2500 entries when history grows beyond 1 MB.
- `tests/test_journal.py`: 10 tests validating rich fields, batching, selectors, collision/restore semantics, legacy formats, repair, rotation.

### Windows (DONE — Phase 2b)
- **Journal v2:** `JournalEntry` writes `v:2`, `id`, `batch` (sweep op id), `rule`, `root`, `size`, `mtime`; `Sweep` mints one batch id and carries it on `SortStats.BatchId`, passing it through `SortFile`. Legacy tab-separated and v1 JSON lines still parse (dropped only when malformed).
- **Selectors:** `Records()`/`Undo()` accept `limit`, `since`/`before` (`30m,2h,7d` or `YYYY-MM-DD[ HH:MM:SS]`), `folder`, `rule` (case-insensitive) and `batch`.
- **Undo improvements:** collision-safe restore (occupied original → `name (1).ext`); already-restored detection via size+mtime (drops the row); warns when the file's size changed; keeps un-reversible rows in the journal; `dryRun` counts only.
- **Rotation & repair:** trims to the most recent ~2500 entries once the file passes ~1 MB; `RepairJournal()` drops malformed lines.
- **Toast hook:** Activity page gains an **"Undo last sweep"** action that reverses the whole most-recent batch (the same call a toast notification's Undo button would make); rows now show the catching rule.
- mtimes are stored as Unix-epoch seconds (matching Python's `st_mtime`), so the two apps share one journal format.
- C# has no local .NET toolchain — validated by the MSBuild CI workflow on every `main` push.

---

## 3. Rule wizard — "powerful, but one simple path" (6 steps)

**Today (v2.x):** 3-page wizard (which files → where → review) + a separate
Advanced form. Most of the engine's filtering power (`enabled`, `match_all`,
`exclude_name_regex`, size/age bounds, priority) is not exposed in any UI, and
the Windows engine ignores those keys entirely.

**v3 target — guided 6-step wizard with real content:**

1. **"What are we tidying?"** — big icon cards (Images, Video, Audio,
   Documents, Archives, Code, Custom) + "Pick files in this folder" +
   "Suggest rules for me". Mascot greets; cards pre-fill from recipes.
2. **"Show me what to catch"** — chip cloud of *real files*; tap to infer
   extensions with a live "catches N of M" count. Manual path is an
   extension **dropdown + type-ahead**, never a comma list. Name matching is
   built from *contains / starts with / ends with / regex* buttons.
3. **"Where should they go?"** — smart name suggestion **or** **Browse…**
   to pick with the file manager (path converted to a name inside the root).
4. **Fine-tune (optional):** size/age filters, exclusions, enabled,
   **match: any / all**.
5. **Priority & scope:** position in the first-match-wins order (▲/▼ +
   conflict warning), this folder's rules vs the shared defaults.
6. **Review with proof:** rule card + **plain-English sentence** + a **custom
   animation** (real files flowing into / past the destination) and an exact
   count; "Save" and "Save & add another".
- **Recipes gallery:** OBS recordings, phone photos, dev downloads…
  prebuilt starting points.
- **Rules page overhaul:** view + plain-English + priority, an **"N sorted"**
  badge from the journal, **edit**, enable toggle, reorder, duplicate, and a
  **live filename tester**.
- **Advanced editor:** extensions as chips, folder picker, grouped fields, live
  English preview, inline validation; keep the raw JSON editor.
- **Journal:** search (filename/glob + date range) on top of selectors,
  in-memory **index** (by rule/folder/batch), restore **preview**, and
  journal **backup / export / import**.
- Windows: same wizard as a native WinUI stepper.

> Full design + sub-slice plan: [`PHASE3_DESIGN.md`](PHASE3_DESIGN.md).

### Status
- Linux: **DONE** — rules page overhaul + 6-step wizard + journal
  search/index/backup/restore all on `main`.
- Windows: **DONE** — schema/engine parity, rules page, the six-step wizard and
  the same journal search/index/backup/restore, built green by MSBuild CI.

---

## 4. Ship it — journal, customization, packaging & release cycle

> Repurposed from "Design & mascot". Full design + sub-slices:
> [`PHASE4_DESIGN.md`](PHASE4_DESIGN.md).

- **Branding:** user-facing name **Funnel**; codename/slug **`gnu-sorter`**
  stays for paths, CLI, packages and repos. One version source, a CHANGELOG,
  and no stale "GNU Sorter"/v2.0 strings.
- **Icons that ship:** one lime "Funnel" master SVG → PNG ladder + multi-frame
  `.ico`; Linux bundles it in a GResource (works without `install.sh`);
  Windows keeps the embedded exe icon, made robust for single-file publish.
- **Better journal:** persistent index, extension/size/status/date filters,
  saved searches, batch grouping, selectable + conflict-aware restore, CSV/JSON
  export/import, configurable retention.
- **Customization:** theme (system/light/dark, lime accent), per-rule
  `icon`/`color`, category icons, real settings.
- **Updates v2:** packaging-aware (Flatpak → defer to `flatpak update`; script
  install → current updater), channel-aware, with an opt-out.
- **Packaging:** a **Flatpak** on Linux (self-hosted OSTree repo on GitHub
  Pages); the self-contained `.exe` stays on Windows. AppImage was dropped.
- **Release cycle:** `indev → alpha → beta → rc → release`, automated via
  `tools/release.py`, tag CI and a nightly `indev` workflow (`RELEASING.md`).

### Status
- Linux: IN PROGRESS — 4.1–4.4, 4.5 (Flatpak) and 4.7 landed. Windows: PENDING
  (mirrors the shared parts; the Flatpak is Linux-only).

---

## 5. Design & mascot — "bring the GUI to life" (was Phase 4)

- **Tunneler mascot:** a cute funnel/tunnel creature. SVG for Linux
  (dark/light aware), PNG set (16→256) for Windows icon + in-app.
- **Placements:** onboarding screen; empty states (no folders / no activity /
  no rules) with speech bubbles; wizard steps (reacts, thumbs-up on save);
  About dialog; "waiting for download" state (napping); "all caught up" state.
- **More images:** per-category icons everywhere, real-file chips in the
  wizard, a before/after illustration on step 1.
- **Windows polish:** keep Mica, add subtle composition animations, same
  mascot assets.
- Keep the lime accent; tighten spacing to system rhythm; dark/light aware.

### Status
- Linux + Windows: PENDING (Phase 5).

---

## Roadmap / status

| Phase | Theme | Linux | Windows |
|------|-------|-------|---------|
| 1 | Download detection (two-tier hold) | ✅ engine+cli+gui+tests on main | ✅ pushed `0b9f50f`, MSBuild CI validating |
| 2 | Journal v2 & restore | ✅ done (`tests/test_journal.py`, CLI selectors, rotation, v2 schema) | ✅ engine+Activity (v2 schema, selectors, collision-safe restore, rotation, repair, sweep-undo) |
| 3 | Rule wizard (6 steps) + rules page + journal search | ✅ done — rules page + advanced editor + 6-step wizard + journal search/index/backup/restore ([design](PHASE3_DESIGN.md)) | ✅ done — schema/engine parity, rules page, 6-step wizard, journal search/index/backup/restore (MSBuild CI green) |
| 4 | Ship it — journal, customization, packaging, release cycle | 🔄 in progress — 4.1–4.4 + 4.5 (Flatpak) + 4.7 done ([design](PHASE4_DESIGN.md)) | ⏳ planned (shared parts; the Flatpak is Linux-only) |
| 5 | Design & mascot | ⏳ | ⏳ |

Every phase lands on `main` incrementally; releases ride on future tags
(remaining pending tag: propagate v2.5.x menu-fix + rename via auto-update).