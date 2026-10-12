# Repository conventions

Read current files rather than assuming these snapshots remain exact:

| Concern | Existing implementation |
| --- | --- |
| Launcher and service wrapper | `apps/kiosk/kiosk.py` |
| Artwork discovery cards | `apps/kiosk/discovery.py` |
| Image cache/widget | `apps/kiosk/images.py` |
| In-game menu | `apps/kiosk/overlay.py` |
| Shared theme/settings | `apps/settings/settings.py` (`THEME`, `SettingsScreen`) |
| Session greeter | `apps/greeter/greeter.py` |
| Dependencies and packaged tests | `apps/kiosk/package.nix` |
| Read-only live integration | `tests/local-kiosk-smoke.py` |

Use `$primary`, `$secondary`, `$foreground`, `$background`, `$surface`, `$panel`, `$warning`, and `$error` from the active theme. The default `kwak` theme has primary `#33ff66`, background black, foreground `#c8ffd4`. Appearance may select another registered theme; keep styles based on variables rather than copying a fixed palette into each application. Use `settings.apply_saved_theme(app)` at app startup to restore the per-user preference. Embedded settings screens inherit their host's active theme and must not reset it on mount.

Keep the launcher compact while retaining larger primary navigation buttons. Discovery uses a full-width card grid and a dedicated Napplet details/store page; Library and Discovery retain independent search inputs and queries. Click or Enter opens discovery details; Play and Install are explicit actions on that page. Keep library management in My Games. Library selection displays details; Play is explicit. Preserve page-specific behavior rather than making every Enter press launch.

My Games uses the same responsive cover-grid and explicit details-page model.
The game page keeps artwork/title/status and actions above independently
scrolling Game Info/Releases content. Back restores the query, selected cover,
and scroll position. Keep sections mounted during refreshes; update artwork
only when its URL changes. Do not invent play-time, social, or achievement data.

Render daemon metadata with `markup=False` or structured Rich text. Reuse in-app settings and restore browsing focus on return. Loading and unavailable-service messages should explain the next usable action.

Retain the owning launcher screen/widget for asynchronous UI delivery. An active modal changes which screen an app query addresses; `App.query_one` can therefore miss underlying launcher controls. Check ownership/mounted state before applying delayed results, and cancel or invalidate delivery on teardown. Do not catch every query error just to conceal a lifecycle bug.

The existing image helper bounds HTTPS input (8 MiB), decoded pixels (16 million), thumbnails (512 pixels), timeout (10 seconds), and cache lifetime (one day). Recheck constants before documenting them. Keep generation/unmount guards and headless protocol protection. Concurrent duplicate URL loads may still need deduplication when profiling justifies it; the cache alone is not a concurrency limit.

## Verification

Resolve the interpreter first:

```sh
nix eval --raw .#kwak-kiosk.python.outPath
```

Use that output's `bin/python3` with `PYTHONPATH=apps/kiosk:apps/settings` for relevant `unittest` modules or the smoke script. Package validation uses:

```sh
nix build .#kwak-kiosk --no-link -L
nix run .#kwak-kiosk -- --local
```

Local UI is backed by the real daemon: Play/Stop/Install/Update mutate live state. Use fake runtimes for automated action tests. The smoke runner permits read-only requests and compares initial/final installed/running state. Include new files in Nix's Git source when necessary without committing unrelated changes.

Normal `nix run` and `nix build` packages skip repository test suites. Request
tests explicitly with `nix build .#checks.x86_64-linux.kwak-kiosk` or
`nix flake check`; do not enable package test phases by default.

## Library ownership

Kwakore owns installed-list sync. `apps/kiosk/library.py` only projects installed
records and `install_sync` diagnostics into the UI; mutations call native
install/uninstall. Do not reintroduce relay library operations in kwak-userd,
launcher sync queues, or launcher-managed remote downloads. Kiosk accounts use
`sync_id = "kwak-library"`; local launches follow their own daemon settings.
