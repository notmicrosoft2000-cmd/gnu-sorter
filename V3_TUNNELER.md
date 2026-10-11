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

### Windows (TODO — Phase 1b)
- Mirror in `SortEngine`: open-file-handle check (CreateFile with
  share-denial) as an *additional* Tier-2 signal — Linux can't do this
  cheaply, Windows can.
- Same `cautious_downloads` setting, same "waiting" visibility in the
  Activity/journal UI, same two-probe default.

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

### Status
- Linux + Windows: PENDING (Phase 2).

---

## 3. Rule wizard — "powerful, but one simple path" (5 steps)

**Today (v2.x):** 3-page wizard (which files → where → review) + a separate
Advanced form.

**v3 target — guided 5-step wizard with real content:**

1. **"What are we tidying today?"** — big icon cards: Images, Video, Music,
   Documents, Archives, Code, Custom. Mascot greets you.
2. **"Show me your files"** — Funnel scans the chosen folder, shows a chip
   cloud of *real files* with icons; tap the ones that belong → extension
   list inferred. (Power path: "type extensions manually".)
3. **"Where should they go?"** — destination picker with category icon +
   auto-suggested name + folder preview.
4. **Fine-tune (optional, collapsed):** size/age filters, match-all vs any,
   exclusions, priority, subfolders. Hidden by default.
5. **Review with live proof:** rule card + real "this will sort N files in
   this folder" count from a scan; mascot thumbs-up; "Save & add another".
- **Recipes gallery:** OBS recordings, phone photos, dev downloads…
  prebuilt starting points.
- Windows: same wizard as a native WinUI stepper.

### Status
- Linux + Windows: PENDING (Phase 3).

---

## 4. Design & mascot — "bring the GUI to life"

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
- Linux + Windows: PENDING (Phase 4).

---

## Roadmap / status

| Phase | Theme | Linux | Windows |
|------|-------|-------|---------|
| 1 | Download detection (two-tier hold) | ✅ engine+cli+gui+tests on main | ⏳ mirror SortEngine + handle-lock check |
| 2 | Journal v2 & restore | ⏳ | ⏳ |
| 3 | Rule wizard (5 steps) | ⏳ | ⏳ |
| 4 | Design & mascot | ⏳ | ⏳ |

Every phase lands on `main` incrementally; releases ride on future tags
(remaining pending tag: propagate v2.5.x menu-fix + rename via auto-update).