# Release cycle

Funnel ships on a **predictable, channel-based** schedule. Every release —
nightly, preview, or stable — is cut the same way, so nothing is improvised.

## Channels

| Channel | Version (repo) | Git tag | Who gets it |
|---|---|---|---|
| **indev** | `X.Y.Z.devN` | `vX.Y.Z-indev.N` | developers; built from every push to `main` |
| **alpha** | `X.Y.ZaN` | `vX.Y.Z-alpha.N` | early preview, opt-in |
| **beta** | `X.Y.ZbN` | `vX.Y.Z-beta.N` | feature-complete testing, opt-in |
| **rc** | `X.Y.ZrcN` | `vX.Y.Z-rc.N` | final soak |
| **release** | `X.Y.Z` | `vX.Y.Z` | everyone (default) |

The version in `gnu_sorter/__init__.py` is **PEP 440** (that is what packaging
tools require); the **git tag** is the human form above. `tools/release.py`
converts between them.

> Channels are **exclusive**. A user on `release` never gets offered an
> `indev` build. The updater compares versions *within* a channel, so the
> ordering of `indev` vs `alpha` strings never matters.

> **Nightly `indev` builds:** the scheduled workflow that publishes a rolling
> `indev` prerelease arrives with the packaging work (Phase 4.5), since its
> value is the Flatpak OSTree repo. Until then `main` is the `indev` version but
> no nightly release is published.

## Branches

- **`main` = the `indev` channel.** Every merge is expected to be green and
  shippable. CI runs on every push and PR.
- **`release/X.Y`** is cut from `main` when a version enters `alpha`. All
  prerelease and stable tags are made **on that branch**. Fixes are
  cherry-picked there **and** back-merged to `main`.
- After a stable release, back-merge `release/X.Y` into `main`.

## Cutting a release

The only tool is `tools/release.py`. It edits the single version source and,
on request, commits, tags and pushes.

```bash
# where are we?
tools/release.py current

# start the next dev cycle on main (indev)
tools/release.py bump minor            # -> X.Y.0.dev0

# enter / advance a channel (on release/X.Y)
tools/release.py cut alpha             # X.Y.Z.dev0 -> X.Y.Za1
tools/release.py cut alpha             # X.Y.Za1 -> X.Y.Za2
tools/release.py cut beta              # X.Y.Za2 -> X.Y.Zb1
tools/release.py cut rc                # X.Y.Zb1 -> X.Y.Zrc1
tools/release.py cut release           # X.Y.Zrc1 -> X.Y.Z
```

Add `--dry-run` to preview, `--commit` to commit, `--tag` to tag,
`--push` to push. `--roll-changelog` (with `cut`) moves the `Unreleased`
section of `CHANGELOG.md` under the new version.

### Typical stable release

```bash
tools/release.py cut release --roll-changelog
tools/release.py cut release --roll-changelog --commit --tag --push
```

## What CI does on a tag

- `Release (Linux)` builds and publishes a GitHub Release on any `v*` tag.
- Tags containing `-alpha`, `-beta`, `-rc` or `-indev` are published as
  **prereleases**; a plain `vX.Y.Z` becomes **latest**.
- The Flatpak repo is refreshed by the `Flatpak (Linux)` workflow: it runs on
  the `v*` tag push (and on a published release), building the tagged source
  and committing the OSTree repo back to `main` (see `PACKAGING.md`). It keys
  off the tag push because a GitHub Release created by another workflow's
  `GITHUB_TOKEN` does not fire `release: published`.

## Checklist

Before tagging a **stable** release:

- [ ] `CI (Linux)` and `Build Windows (x64)` are green on `main`.
- [ ] `tools/release.py current` shows the intended channel.
- [ ] `CHANGELOG.md` has real entries under `Unreleased` (then
      `--roll-changelog` moves them).
- [ ] Version bumped on both repos to the same `X.Y.Z`.
- [ ] Download links / `docs/` mention the right version.

## Windows

Windows follows the **same** channel/version scheme on its own repo
(`gnu-sorter-windows`); the tag drives the same tag-publish CI. The executable
name (`gnu-sorter-windows.exe`) and the auto-update asset name are **fixed** —
do not rename them, or the updater and download link break.
