# Phase 3 — Rule wizard & rules page overhaul (design)

> Working design for v3 "Tunneler" Phase 3. Linux lands first, then Windows
> mirrors it. UI-only metadata never changes how files are *matched*; the
> shared `rules.json` schema stays the source of truth for both apps.

## 1. Goals

- **More powerful**: expose the filtering the engine already supports
  (`enabled`, `match_all`, `exclude_name_regex`, size/age bounds, priority) and
  finish the schema so both platforms behave identically.
- **Easier**: a guided, 6-step wizard that a first-time computer user can finish;
  never make anyone type a regex or a comma-separated list.
- **Friendlier**: plain-English explanations + a custom animation that shows,
  before saving, which real files would move and which would not.
- Lay the **Phase 4** groundwork (design tokens, category icons, mascot seams).

## 2. Rule schema — current vs target

Shared keys (see `config.py`), all optional except `name`:

| key | meaning | Linux | Windows today |
|---|---|---|---|
| `name` | destination folder (may be `Photos/Screenshots`) | ✅ | ✅ |
| `extensions` | end-of-name suffixes | ✅ | ✅ |
| `match_name_regex` | name contains / matches | ✅ | ✅ |
| `enabled` | `false` → skipped entirely | ✅ engine | ❌ ignored |
| `match_all` | catch-all (matches everything) | ✅ engine | ❌ ignored |
| `exclude_name_regex` | veto if the name matches | ✅ engine | ❌ ignored |
| `min_size_mb` / `max_size_mb` | size bounds | ✅ engine | ❌ ignored |
| `modified_within_days` | recency bound | ✅ engine | ❌ ignored |
| `match_mode` | `any` (OR, default) or `all` (AND) | ✅ engine | ✅ engine |
| `icon` / `color` | UI-only presentation metadata | ⏳ add | ⏳ add |

> **Priority = array order.** First enabled positive rule that matches wins.
> `match_all` is the catch-all fallback and normally goes last.

`normalize_rule` currently rebuilds each rule from known keys, so UI metadata
(`icon`/`color`) is dropped on save — the schema pass must preserve them.

### `match_mode` — decided: any/all toggle (default `any`)

Right now every rule is an **OR**: a file matches if its extension is in the
list **or** its name matches the regex.

- **Any** (today, default): broad. "`png`/`jpg` **or** the name says screenshot."
- **All**: precise. "must be an image **and** the name says screenshot **and**
  bigger than 2 MB" — e.g. a rule for *large* screenshots only.

**Decision (user, Phase 3):** add a per-rule **Match: any / all** toggle in the
fine-tune step, stored as `match_mode` (default `any`, so nothing changes for
existing rules). **Implemented** in both engines: `any` = OR across the
specified matchers; `all` = every *specified* matcher must hit (the `match_all`
catch-all counts as always true; a rule with no matchers never matches). The
advanced editor exposes it as a plain-language "Require every condition" switch.

## 3. The 6-step wizard

### Step 1 — "What are we tidying?"
Big icon cards: Images · Video · Audio · Documents · Archives · Code · Custom,
plus two magic entries: **"Pick files in this folder"** and **"Suggest rules for
me"** (scan → cluster extensions → propose a set). Mascot greets. Each card
pre-fills name + extensions from the recipe library.

### Step 2 — "Show me what to catch"
A chip cloud of **real files** in the chosen folder (icon + name + extension,
frequency-sorted). Tap chips → extensions inferred; a live line reads
*"This catches 412 of 587 files."* Manual path: an **extension dropdown +
type-ahead** (no commas) that only offers extensions actually present. Then a
plain-language name matcher — **contains / starts with / ends with / matches
regex** — built from buttons, not typed regex.

### Step 3 — "Where should they go?"
Smart suggestion (reuse `_suggest_folder`) + **Browse…** opening the native file
manager (`Gtk.FileDialog.select_folder` on Linux, `FolderPicker` on Windows).
The chosen path is converted to a name **inside the watched root** (relative if
inside, basename otherwise) and validated live with `safe_category_name`.
Live folder-tree preview.

### Step 4 — "Fine-tune" (optional)
Size bounds (min/max MB) · Age ("only newer than N days") · Exclusions ·
Enabled toggle · **Match: any / all**.

### Step 5 — "Priority & scope"
Because first-match-wins, this step shows the rule's position in the ordered
list with up/down controls and a warning when a higher rule would steal files.
Scope: this folder's own rules vs. the shared defaults. (Subfolders/recursion is
deferred — see decisions.)

### Step 6 — "Review with proof"
Rule card + **plain-English sentence** + a **custom animation** showing real
files flowing into (or past) the destination, with an exact count. Buttons:
**Save**, **Save & add another**.

## 4. The plain-English engine

A tiny, shared describer that turns a rule dict into friendly text. Handles the
common cases and falls back gracefully:

- Literal regex → *“the name contains ‘screenshot’”*.
- Alternation of literals → *“the name contains one of: wallpaper, white.sur”*.
- Complex regex → *“the name matches /…/”*.
- Adds bounds: *“…and between 2 and 50 MB, and modified in the last 30 days.”*
- Not this: *“Everything else goes to Needs-review.”*

Used by: wizard step 6, the rules page subtitle, and the advanced editor preview.
Implemented once per platform (`describe_rule` / `RuleText.Describe`) and tested.

## 5. The custom animation (Step 6)

Not a stock spinner. A small custom widget (Linux: `Gtk.DrawingArea` drawn in a
`Gtk.Snapshot` tick callback; Windows: a `CompositionTarget` / `CanvasControl`)
that:

1. fans out a handful of **real file chips** for the folder,
2. funnels the matching ones into the destination folder icon,
3. leaves the non-matching chips in place (dimmed),
4. settles on the exact counts.

Respects **reduced-motion** (falls back to a static before/after frame) and
dark/light. Data comes from the engine's `classify()` (one pass over the folder).

## 6. Advanced editor — refine the rough edges

- **Extensions as chips**: dropdown/type-ahead to add, click to remove — no
  comma typing.
- **Destination**: entry + **Browse…** folder picker (same as wizard step 3).
- **Grouped fields** (Match · Where · Refine) inside a `Gtk.Expander`/
  `Expander`, valid-looking form instead of a flat row list.
- **Live plain-English preview** under the fields.
- **Inline validation** with plain messages (duplicate name, bad regex,
  unsafe folder).
- Keep the raw **JSON editor** for power users (decision 5).

## 7. Rules page overhaul

- **View**: plain-English subtitle per rule + **priority number**.
- **“N sorted”** badge: how many journal entries this rule has moved (from the
  journal index, `rule_counts()`).
- **Edit** (Linux lacks it today): opens the refined editor pre-filled.
- **Enable/disable** toggle per rule.
- **Reorder** with ▲/▼ buttons (decision 3); first-match-wins explained in a hint.
- **Duplicate** (reuse `duplicate_rule`).
- **Live tester**: type a filename → *“→ Photos (rule #3).”* via `classify()`.

## 8. Journal: search, indexing, better restore, backup

- **Index**: read the journal once and build lookups (by rule, by folder, by
  batch, by time) so counts and the rules page are O(1) per query, and restore
  previews are instant.
- **Search**: selectors today are `limit/since/before/folder/rule/batch`; add
  **filename/glob** and an explicit **date range**, on the CLI and in Activity.
- **Better restore**: a restore **preview** (dry-run listing with sizes and
  conflicts) before committing; restore into a chosen target folder; keep the
  existing collision-safe / verification logic.
- **Backup**: copy the journal to `history.log.bak` before rotation; add
  **export/import** of the journal (and rules) plus a Settings/Activity entry.

## 9. Phase 4 groundwork (build the seams now)

- **Design tokens** module (colors incl. lime accent, spacing rhythm, radii,
  type scale, dark/light) — stop hard-coding.
- **Category icon map** keyed by a shared category id (SVG on Linux, Fluent
  asset on Windows) used by wizard, rules and presets.
- **Mascot seam**: `mascot(state)` returning greeting / thinking / thumbs-up /
  napping / all-caught-up; art drops in Phase 4.
- **Empty states** for folders/rules/activity (guidance + primary action + mascot).

## 10. Sub-slices & order

1. **Schema + plain-English + classify/counts** — `config.py`, `engine.py`,
   tests. *(Windows parity follows.)* — **DONE**
2. **Rules page overhaul** (Linux) + refined advanced editor. — **DONE**
3. **6-step wizard** (Linux) incl. folder picker + custom animation. — **DONE**
4. **Journal search/index/backup/restore** (CLI + Activity). — **DONE**
   (`JournalIndex`, name/glob + batch + date selectors, restore preview &
   `--restore-into`, `--backup-journal`, journal/rules export & import, and an
   Activity search bar + preview-then-commit restore dialog.)
5. **Windows mirror** of 1–4 (native stepper, Fluent icons).
6. Mark Phase 3 done; Phase 4 art lands on the seams above.

## 11. Locked decisions

- Q2 **Defer recursion/subfolders.**
- Q3 **Up/down buttons** for reorder.
- Q4 **Add `icon`/`color`** to the schema.
- Q5 **Keep the raw JSON editor.**
- Q6 **Do both** (wizard + rules-page overhaul).

**Resolved:** Q1 `match_mode` any/all — added as a per-rule toggle, default `any`
(both engines + advanced editor). See §2.
