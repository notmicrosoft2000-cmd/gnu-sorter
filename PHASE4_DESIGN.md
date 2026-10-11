# Phase 4 — "Ship it": journal, customization, packaging & a real release cycle

> Repurposed Phase 4 (was "Design & mascot"). Linux lands first, then Windows
> mirrors the shared features. **User-facing name: _Funnel_**. **Codename /
> slug: `gnu-sorter`** (paths, CLI command, package names, repo URLs stay
> `gnu-sorter`). Only the shipped **display name** is Funnel.
>
> The mascot / visual-overhaul art moves to **Phase 5**; only the small seams
> Phase 4 already needs (category icons, one real app icon) land here.

## 1. Goals

1. **Better journal** — make the history genuinely useful at scale (index,
   filters, saved searches, richer restore).
2. **Customization** — themes, per-rule icon/color, real settings.
3. **A real icon** — a lime "Funnel" icon that ships in the binary and every
   package (Linux currently ships a *blue folder* and only works after
   `install.sh`).
4. **Check & install updates** — packaging-aware, channel-aware, with an
   opt-out; consistent across Linux and Windows.
5. **Package it** — Flatpak **and** AppImage on Linux (plus the existing
   self-contained `.exe` on Windows).
6. **A predictable release cycle** — `indev → alpha → beta → rc → release`,
   automated, documented, so every future release follows the same script.

## 2. Current state (baseline, verified)

| Area | Linux (`gnu-sorter`) | Windows (`gnu-sorter-windows`) |
|---|---|---|
| Display name | "Funnel" (`APP_NAME`) | "Funnel" (title/Product/AssemblyTitle) |
| App id | `io.neptunepc.Funnel` | none (unpackaged) |
| Icon shipped | blue folder SVG + `.ico`; window icon only resolves **after** `install.sh` rasterizes into hicolor; `tray.py` references a **missing** `data/icons/gnu-sorter.png` | `Assets/Icon.ico` (7 frames) embedded via `<ApplicationIcon>`; runtime taskbar icon read from `AppContext.BaseDirectory/Assets/Icon.ico` |
| Update check | `updater.py` (GitHub Releases *tarball* → rsync into install dir); always-on, no config; **only works for `install.sh` installs** | `Services/UpdateService.cs` (GitHub Releases API → self-replace `.exe` + restart); launch + manual |
| Packaging | **none** — `install.sh`/`install-gnu-sorter.sh` only | self-contained single-file `gnu-sorter-windows.exe` via CI |
| Version source | duplicated: `gnu_sorter/__init__.py` **and** `pyproject.toml` | `csproj <Version>` |
| Stale branding | README says "v2.0"; install/uninstall comments "v2.0" | `docs/index.html` still "GNU Sorter"; `app.manifest` identity `2.0.0.0`; About version hard-coded |
| Release CI | `release.yml` on `v*` → GitHub Release with 3 script files; `ci.yml` on main/PR | `build-windows.yml` on main/PR/`v*` → artifact + Release on tags |
| Release notes | `generate_release_notes: true` | `generate_release_notes: false` |
| Changelog | none | none |

`.gitignore` already anticipates `*.flatpak`, `*.AppImage`, `*.snap`.

## 3. Sub-slice plan

### 4.1 — Branding & version single-source
- **One version source** per app, read at runtime and injected into the builds:
  Linux `__init__.__version__` is canonical and `pyproject.toml` reads it
  dynamically (or vice-versa via `setuptools_scm`); Windows keeps `csproj`.
- Fix stale strings: README `v2.0`, install/uninstall comments, Windows
  `docs/index.html` ("GNU Sorter" → "Funnel"), `app.manifest` identity.
- Bind the Windows About version to `UpdateService.CurrentVersion()` (kills the
  hard-coded `2.5.0`).
- Add **CHANGELOG.md** (Keep a Changelog) to both repos.
- Decide the Windows exe/asset name (see **Q3**).

### 4.2 — Icons that actually ship (the lime "Funnel" mark)
- A single **master SVG** in the lime accent (`BRAND = #a3e635`), dark/light
  aware; a build script exports the PNG ladder (16/24/32/48/64/128/256/512) and
  the multi-frame `.ico`.
- **Linux**: bundle the icon in a **GResource** so the window/tray icon resolves
  without `install.sh`; `set_default_icon_name`; fix the tray's missing PNG
  candidate; ship the icon in Flatpak/AppImage under the app-id name.
- **Windows**: keep `<ApplicationIcon>` (already embeds into the exe); make the
  runtime taskbar icon robust for `PublishSingleFile`
  (`IncludeAllContentForSelfExtract=true`, or load the `.ico` as an embedded
  resource) so a lone `.exe` still shows it.
- This is the "executable binary has the app icon" requirement, end to end.

### 4.3 — Better journal
- **Persistent index** so search/stats are fast at scale (not just in-memory):
  build once, cache, invalidate on append. Keys: rule, folder, batch, extension,
  time, size.
- **Search & filters**: filename/glob + date range (already partly there) **plus**
  extension, size range, status (moved/restored/failed), rule, folder, batch;
  **saved searches**.
- **Grouping & stats**: group by batch; per-rule and per-folder totals (feeds the
  rules page "N sorted").
- **Richer restore**: select a subset, per-conflict resolution
  (overwrite / rename / skip), restore into an arbitrary folder, always a
  dry-run preview first.
- **Export/import**: CSV **and** JSON; import back into the journal.
- **Configurable retention**: max entries / max size / rotation policy in
  settings (today it is hard-coded).

### 4.4 — Customization & settings
- **Appearance**: follow system / light / dark; lime accent kept, with the
  option to change it.
- **Per-rule `icon`/`color`** pickers (schema already carries `icon`/`color`;
  the UI does not expose them yet).
- **Category icon set** (shared category id → SVG on Linux, Fluent on Windows).
- **Settings that matter**: watch interval, notifications, dry-run default,
  retention (4.3), **update channel** + **check-for-updates** toggle (4.6),
  start-hidden/tray.
- Windows `Notifications` pref currently does nothing (no toast implementation)
  — either wire it or remove it.

### 4.5 — Packaging: Flatpak + AppImage (+ exe)
- **Flatpak** — `io.neptunepc.Funnel` (or the Flathub-valid id, see **Q1**):
  manifest, AppStream `metainfo.xml`, `.desktop` named by app id, icons under
  the app-id name, GNOME runtime. Built in CI with `flatpak-builder`, exported
  to a hosted OSTree repo.
- **AppImage** — recipe via `linuxdeploy` + `linuxdeploy-plugin-gtk` (GTK4 /
  libadwaita) producing `Funnel-<ver>-x86_64.AppImage`, including desktop file,
  icon and metainfo.
- **Windows** — keep the unpackaged self-contained `.exe` (works today); MSIX is
  an optional later add.
- Keep `install.sh` for devs/CI but present Flatpak/AppImage as the primary
  downloads.

### 4.6 — Updates v2 (packaging-aware)
- Detect how the app was installed at runtime:
  - **Flatpak**: `/.flatpak-info` exists → the app is read-only; detect "update
    available" and point the user at `flatpak update` / software center (never
    self-replace inside the sandbox).
  - **AppImage**: `$APPIMAGE` set → replace the AppImage file
    (AppImageUpdate/zsync or full-file swap).
  - **script install**: keep the existing `updater.py`/`UpdateService`.
- **Channel-aware**: only offer a build whose channel matches the user's
  channel (see 4.7); default = `release`. Gate by channel **and** version so the
  SemVer ordering between `indev`/`alpha`/`beta` never matters.
- **Opt-out**: `check_for_updates` setting + a manual "Check for updates"
  everywhere (Linux CLI `--update-check`/`--update` already exist).

### 4.7 — Release cycle & channels (the "predictable" part)
See §4 below. Deliverable: `RELEASING.md` + CI + a bump/tag script.

## 4. Release cycle

### 4.1 Channels & versions

SemVer 2.0.0 with pre-release identifiers, one channel each:

| Channel | Version tag | Audience | Source | Artifacts |
|---|---|---|---|---|
| **indev** (nightly) | `X.Y.Z-indev.<build>` | developers / the brave | every push to `main` | AppImage + exe, rolling prerelease |
| **alpha** | `X.Y.Z-alpha.N` | early preview, opt-in | tag on `release/X.Y` | AppImage, exe, flatpak (dev remote) |
| **beta** | `X.Y.Z-beta.N` | feature-complete testing, opt-in | tag on `release/X.Y` | all artifacts |
| **rc** | `X.Y.Z-rc.N` | final soak | tag on `release/X.Y` | all artifacts |
| **release** | `X.Y.Z` | everyone (default) | tag on `release/X.Y` | all artifacts + Flathub + site |

> **Ordering note:** channels are *exclusive*; the updater compares version
> **within** a channel, so the lexicographic ordering of `indev` vs `alpha` is
> irrelevant. A user on `release` is never offered an `indev` build.

### 4.2 Branch & tag strategy (trunk-based)
- `main` **is** the `indev` channel — always green, always shippable.
- Feature work: PR → CI (`ci.yml` / `build-windows.yml`) → merge to `main`.
- Stabilize: cut **`release/X.Y`** from `main`. Prereleases (`-alpha.N`,
  `-beta.N`, `-rc.N`) are tagged there; fixes are cherry-picked to it and
  back-merged to `main`.
- Ship: tag **`vX.Y.Z`** on `release/X.Y` → stable Release + Flathub update.
- After each stable, **back-merge** `release/X.Y` into `main`.

### 4.3 Automation
- **`tools/release.py`** (one per app, or shared): bumps the single version
  source, rolls `CHANGELOG.md` (Unreleased → version), commits `release: vX.Y.Z`,
  tags, pushes. `--channel indev|alpha|beta|rc|release` picks the version suffix.
- **CI on tag** builds the artifacts for that channel and creates a GitHub
  Release: `prerelease: true` for `alpha`/`beta`/`rc`, `latest` only for stable;
  release notes from the CHANGELOG section.
- **Nightly workflow** (schedule + push to `main`) publishes a rolling
  `indev` prerelease and uploads the AppImage.
- **Cadence**: one stable per phase, with as many `alpha`/`beta`/`rc` cuts as
  the phase needs. Junior rule: never tag stable while `ci.yml` is red.

### 4.4 Version bumping in the repo
- Linux: `__init__.__version__` is the single source; `pyproject` reads it
  dynamically. Windows: `csproj <Version>` is the single source.
- No more duplicated literals; `tools/release.py` edits the one place.

## 5. Sub-slice order

1. **4.1 Branding + version single-source + CHANGELOG.** (small, unblocks CI)
   — **Linux DONE** (`9b652bd`: `__version__` single-source, dynamic
   `pyproject`, `CHANGELOG.md`, stale v2.0 strings fixed, version guard test).
2. **4.2 Icons** (master SVG → PNG/ICO → GResource → packages).
   — **Linux DONE** (kept the blue-folder design): committed the hicolor PNG
   ladder + a `data/icons/gnu-sorter.png` for the tray, moved `install.sh` onto
   the single-source `data/gnu-sorter.desktop`, removed the rsvg/inkscape
   requirement, and added `tests/test_assets.py`. Packaging export lands in 4.5.
3. **4.3 Journal** (index, filters, restore, retention).
   — **Linux DONE**: new `ext` / `min_size` / `max_size` selectors (CLI
   `--journal-ext`, `--journal-min-size`, `--journal-max-size`, with `K/M/G`
   parsing), restore `--on-conflict rename|overwrite|skip`, the same controls
   in the Activity filter bar + Restore dialog, and configurable retention
   (`history_max_entries` / `history_max_bytes`, now in Settings).
4. **4.4 Customization & settings.**
   — **Linux DONE**: Settings → Appearance (System / Light / Dark, remembered,
   applied via `Adw.StyleManager`); per-rule **accent colour** picker in the
   advanced editor (`Gtk.ColorDialogButton`) with a coloured dot on the rules
   page. Existing `icon`/`color` metadata is still preserved through
   `normalize_rule`.
5. **4.6 Updates v2** (packaging-aware, channel-aware, opt-out).  ← after 4.5 so
   the install-type detection targets real formats.
6. **4.5 Packaging** (Flatpak + AppImage) on Linux; exe stays on Windows.
7. **4.7 Release cycle** (`RELEASING.md`, `tools/release.py`, nightly + tag CI).
   — **Linux DONE** (`tools/release.py` + `tests/test_release.py`,
   `RELEASING.md`, channel-aware `release.yml`; nightly deferred to 4.5).
8. **Windows mirror** of the shared parts (4.1–4.4, 4.6); Flatpak/AppImage are
   Linux-only.

## 6. Resolved decisions

- **Q1 Flatpak target — self-hosted repo on GitHub Pages.**
  The repo lives at `https://notmicrosoft2000-cmd.github.io/gnu-sorter/flatpak/`
  (`…/repo` OSTree + `io.neptunepc.Funnel.flatpakrepo`), keeping the existing
  `io.neptunepc.Funnel` id. CI (`flatpak-builder` + `flatpak build-export` +
  `ostree summary -u`) publishes it to the Pages branch on each release tag.
  *Home server:* kept as an optional **custom-domain mirror** later (e.g.
  `flatpak.neptunepc.io` serving the same OSTree repo) — GitHub Pages stays the
  canonical remote so a home-server outage never breaks updates. Optional GPG
  signing of the repo can be added when the mirror lands.
- **Q2 Version scheme — confirmed.** `X.Y.Z-alpha.N` / `-beta.N` / `-rc.N` +
  `X.Y.Z-indev.<build>`, channels exclusive. **Next release: `3.0.0`** (the
  current v3 "Tunneler" work), then `3.x` line.
- **Q3 Windows exe — keep `gnu-sorter-windows.exe`.** No change to
  `UpdateService.AssetName`, the direct-download link, or the CI artifact name.
- **Q4 Journal + customization scope — as listed** in §4.3/§4.4.

## 7. Explicitly deferred to Phase 5
- Tunneler **mascot**, onboarding screens, empty-state illustrations, the
  full visual overhaul / before-after hero art.
