# Packaging Funnel

Funnel ships to Linux users as a **Flatpak**. Windows keeps the classic
`gnu-sorter-windows.exe` (see the `gnu-sorter-windows` repo); there is no
AppImage.

- **App id / ref:** `io.neptunepc.Funnel`
- **Runtime:** `org.gnome.Platform` (currently `//49`)
- **Public repo:** <https://notmicrosoft2000-cmd.github.io/gnu-sorter/flatpak/>

We host our own [OSTree](https://ostreedev.github.io/ostree/) repository on
GitHub Pages instead of distributing a git checkout. GitHub Pages serves the
site from `docs/`, so the published repo lives at `docs/flatpak/` and is
refreshed by the [`Flatpak (Linux)`](.github/workflows/flatpak.yml) workflow.

## Why this is simple for us

Funnel is pure Python plus GTK4/libadwaita. The **GNOME runtime already
contains** `python3`, PyGObject (`gi`) and GTK4/libadwaita, so the Flatpak
build just copies the source — there is no compilation and no `pip` step. The
optional helpers (`watchdog`, `pystray`, `platformdirs`) are not bundled;
Funnel degrades gracefully without them.

The manifest lives at [`packaging/flatpak/io.neptunepc.Funnel.yml`](packaging/flatpak/io.neptunepc.Funnel.yml)
and the entry point at `packaging/flatpak/funnel-launcher.sh`.

## Install (users)

```sh
flatpak remote-add --if-not-exists funnel \
  https://notmicrosoft2000-cmd.github.io/gnu-sorter/flatpak/io.neptunepc.Funnel.flatpakrepo
flatpak install funnel io.neptunepc.Funnel
flatpak run io.neptunepc.Funnel
```

If the repo is published **unsigned**, add it with `--no-gpg-verify`:

```sh
flatpak remote-add --if-not-exists --no-gpg-verify funnel \
  https://notmicrosoft2000-cmd.github.io/gnu-sorter/flatpak/io.neptunepc.Funnel.flatpakrepo
```

## Build locally

`flatpak-builder` is required (and the GNOME SDK, which is downloaded on first
use):

```sh
flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo
flatpak install flathub org.gnome.Sdk//49 org.gnome.Platform//49

flatpak-builder --user --install --force-clean \
  build-dir packaging/flatpak/io.neptunepc.Funnel.yml
flatpak run io.neptunepc.Funnel
```

To reproduce exactly what the workflow publishes:

```sh
rm -rf docs/flatpak
flatpak-builder --repo=docs/flatpak --force-clean \
  build-dir packaging/flatpak/io.neptunepc.Funnel.yml
flatpak build-update-repo docs/flatpak
```

## Publishing

The [`flatpak.yml`](.github/workflows/flatpak.yml) workflow runs when a `v*`
**tag is pushed** — or when a GitHub release is published, or manually from the
Actions tab. The tag push is the reliable trigger: a GitHub Release created by
another workflow's `GITHUB_TOKEN` does not fire `release: published`. It builds
the OSTree repo into `docs/flatpak/`, writes a `.flatpakrepo` file and a small
landing page, then commits the result back to `main`.

### Signing

Unsigned by default. To sign, add two repository secrets:

| Secret | Meaning |
| --- | --- |
| `FLATPAK_GPG_PRIVATE_KEY` | ASCII-armoured GPG private key |
| `FLATPAK_GPG_KEY_ID` | The key's id/fingerprint |

With those present the workflow signs the repo and embeds the public key in the
`.flatpakrepo` file, so users no longer need `--no-gpg-verify`.

## Sandbox limitations

Inside the sandbox some desktop integrations are unavailable by design:

- **System tray** — `pystray` is not bundled, so the tray icon is absent. The
  main window and background watching still work.
- **Run at login** — the app manages a `systemd --user` unit; that cannot be
  written from inside the Flatpak. Use Funnel's own `--watch` mode in a
  terminal, or launch it at login through your desktop's autostart, until a
  proper background portal integration lands.
- **File access** — the manifest grants `home` plus `/media` and `/run/media`,
  which covers the folders Funnel is meant to tidy.

## Versioning

Pre-release channels (`indev → alpha → beta → rc → release`) are described in
[`RELEASING.md`](RELEASING.md). The Flatpak version always follows
`gnu_sorter.__version__`.
