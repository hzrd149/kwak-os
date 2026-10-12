# Game kiosk implementation

## Terminal images

The kiosk package includes `textual-image` and Pillow, and a reusable
`TerminalImage` widget in `apps/kiosk/images.py`.

Automatic rendering uses the terminal's supported image protocol; Kitty uses
pixel graphics, and character rendering provides a fallback. The explicit
halfcell mode is also useful for headless tests. Import `images` before starting
the Textual application so terminal detection happens before its input loop.
See [textual-image's integration documentation](https://github.com/lnqs/textual-image#textual-integration).

Future game cards and screenshot previews can mount `TerminalImage(source)`
and call `set_source(path_or_https_url)` when selection changes. Loading happens
off the UI thread, clears the previous picture immediately, and ignores stale
results. Empty and failed sources show placeholders. HTTPS images are cached
for one day under `$XDG_CACHE_HOME/kwak/images`, with hashed filenames and atomic
writes. Downloads are limited to 8 MiB, decoded images to 16 million pixels,
and previews to 512 pixels per side; animations use their first frame.

This prepares image rendering without changing the library layout. Napplet
icons still need an explicit source from kwakore's control API; the widget
does not infer paths or download game artifacts itself.

## Terminal sizing

The library, overlay, Settings, greeter and session dialogs use 18-point
DejaVu Sans Mono. The VM fixes its 1600×1200 output at scale 1.25, yielding
a 1280×960 logical desktop instead of relying on virtual monitor density.
The library fills workspace one; the overlay starts at 80×28 cells and
scrolls its menu when needed. Physical displays retain automatic scaling.
Rebuild the VM to apply the display scale and packaged terminal settings.

## Local development with an existing kwakore

The TUIs can run against the current user's installed `kwak`; a VM is not
required for catalog and runtime integration checks. From the repository root:

```sh
nix build .#kwak-kiosk -o result-kiosk
nix eval --raw .#kwak-kiosk.python.outPath > /tmp/kwak-kiosk-python-path
"$(cat /tmp/kwak-kiosk-python-path)/bin/python3" tests/local-kiosk-smoke.py
```

The smoke runner uses the real `kwak --json` transport, checks My Games and
Find Games navigation, pagination and search at several terminal sizes,
including the VM's approximate 88×33 library and 80×28 overlay. It permits
only status, installed, windows, detail and discover requests, disables account-library
sync, isolates screenshot state, and verifies installed records and active
windows are unchanged. It reports counts rather than personal game details.
Discovery may read configured relays and update kwakore's catalog cache.
The overlay test checks its empty-target menu; game-specific actions and
compositor integration still need the graphical kiosk tests.

For an interactive library against the local runtime, using the local daemon’s native library and sync configuration:

```sh
nix run .#kwak-kiosk -- --local
```

When launched from a terminal, this uses that terminal's font settings and
avoids needing Nix graphics drivers on a non-NixOS host. The interactive
mode supports normal local Install and Play actions when selected. `KWAK_CLI`
can select another CLI binary. The packaged
`result-kiosk/bin/kwak-kiosk` runs the full signed-in kiosk flow and therefore
uses `kwak-userd` as the system signer for native kwakore sync.

The agreed experience is a public kiosk: swipe a Nostr card, see your game
library immediately, download missing games concurrently, and play each when
ready. Accounts remain temporary by default, retaining the existing greeter
option for persistence. New Unix sessions open the library; swiping back to an
existing session resumes its current position.

All system interfaces use the existing green Textual theme with mouse and
keyboard controls. The library and store provide primary navigation. Windows
opens a system overlay; Super+Q opens a terminal as an escape hatch. Hyprflow
provides workspace selection between running applications. Transparent kitty
backgrounds are used for the overlay. Focus loss is the requested game pause
behavior for the overlay; it does not guarantee resource suspension.

## First implemented slice

The launcher library uses a responsive cover grid and a separate game page.
Clicking a cover or pressing Enter opens details; launching is an explicit Play
action. The page shows the description, installed release, available update, known releases,
and running instances. Release checks run automatically; when a newer release is
available, Update installs it through kwakore.
Stop closes the selected game's exact runtime window IDs, including background
windows; running state is refreshed without contacting discovery relays.

Kwakore keeps the installed release until an explicit update. Its public CLI
and control protocol currently do not support choosing an older release or
setting a version pin. The launcher displays known releases but cannot yet
restore a particular release on another kiosk. That requires a version-targeted
install/update operation in kwakore. Native sync already carries installed release pins. Social sections can be added to the details pane
when the runtime exposes them.

`apps/kiosk` provides My Games, Find Games, local search, paginated catalog
loading, Install, Play, and Play Now. It uses the pinned kwakore package's
`kwak --json` CLI, retaining its socket ownership, peer validation, protocol
checks, and canonical address handling. Network operations run outside the UI
thread. Failed reads display an error instead of an empty-library message.
Install operations for different games can run concurrently; there is no
invented percentage because the current API returns a final outcome only.

Both the initial runtime pin `b4f95e558eff7f8be20d8373e35c5be3145c4759`
and the current pin `b8990c3e8f61f24b94450736ffeb0dcac080fde3` support `napplet.try`: verified game files are downloaded into memory, no
installed record or desktop entry is created, and a trial window is launched.
The runtime exposes discovery, installed records, launch, and window listing
through its version 1 control protocol. No runtime source changes are needed
for this first slice.

The Nix package installs `kwak-kiosk` and `kwak-overlay`. Nostr desktop startup
opens the library on workspace 1. The overlay is a separate game menu: Resume,
Exit Game, Take Screenshot, Publish Screenshot, Back to Library, Switch Games
through Hyprflow, Settings, and Session. It contains no game catalog or library
rows. Publishing requires a confirmation with the saved filename, dimensions,
and optional caption; Cancel is focused by default. Per-session locks
prevent duplicate library or overlay windows. Existing administrator and ISO
startup behavior is retained. Super+E no longer opens the file manager.

### Game screenshots

Screenshot actions belong to the game that was active before the system overlay
opened. The overlay checks the process owner, executable, PID start time and
compositor window identity. Installed games expose an opaque
`KWAKORE_INSTANCE_ID` matching the runtime windows list and use the exact stop
RPC. Play Now games expose a numeric renderer ID instead, so Exit Game closes
only that verified compositor window. Both retain the canonical game address
without trusting the title or affecting another running game.
The current runtime has no capture RPC, so the kiosk captures through the
compositor after hiding the overlay. A screenshot must contain the game rather
than the system menu.

The account service exposes `publish_screenshot` with `filename`, `caption`,
and the game's canonical `game` address. Publication is a separate user action
from local capture. Files are read only from the requesting account's
`~/.local/share/kwak/screenshots` directory: each path component is opened
without following symlinks, and the PNG must be a regular file owned by that
account, no larger than 16 MiB or 40 million pixels. Captions are limited to
1000 characters. The request cannot choose another account or an arbitrary
file path, and the kiosk gains no generic signing permission.

Publication uses the account's media servers and write relays. The backend
signs a Blossom kind-24242 authorization bound to the PNG's hash, uploads it
over HTTPS, and checks the returned hash, size and URL origin. Upload redirects
are refused. It then signs a kind-1 note containing the caption and image URL,
`imeta` image metadata, and an `a` tag pointing to the game. The same held or
bunker identity signs relay authentication when needed. Success requires a
relay acknowledgment; no key or bunker credential is returned to the overlay.

The local [real-game fixture](../tests/napplet-fixture.md) includes a trusted
HTTPS Blossom upload endpoint and media-server event, allowing screenshot
publication tests without posting to external servers.

## Native library sync and session pause

Kwakore owns the installed library and all cross-device syncing. Kiosk accounts
use `settings.sync_id = "kwak-library"` with the existing system signer. Local
launches use the local daemon's signer and sync settings. To join the same
library on another machine, use the same identity and run
`kwak settings set sync_id kwak-library` there.

The daemon encrypts its kind 30078 list to the account using NIP-44, includes
pinned versions, merges on first activation, follows remote changes, and retries
in the background. Installation, updates and uninstallation go through kwakore.
Removal uninstalls on participating devices and closes affected game windows.
Play Now remains a trial. The launcher reads installed games and `install_sync`
diagnostics; it does not publish lists, persist sync intents, or reconcile
remote downloads. Routine UI polling only reads daemon state.

The former plaintext `kwak-napplets` event and
`$XDG_STATE_HOME/kwak/library/state.json` are no longer read or written. They are
left untouched. Already installed games enter native sync through its first-pass
merge. Prototype entries that were never installed, including pending additions,
need to be installed explicitly to join the native library. Do not reuse
`kwak-napplets` as the native sync ID: its plaintext payload is incompatible with
the encrypted native format. Native sync does not support NIP-42 AUTH-only relays;
configure a write relay that accepts the account's sync events without AUTH.

Session exit no longer runs a separate launcher sync pass. Kwakore owns sync
until its service stops; ending a temporary account retains the existing deletion
lifecycle. Account signing and screenshot publication remain in kwak-userd.

## Remaining rollout and hardware checks

- Exercise longer game sessions and GPU/audio recovery on target hardware.
  Real WebKit fixtures now validate downloads, temporary play, screenshots and
  session recovery; they do not certify every game or physical GPU. Freeze/thaw failure is
  logged and switching continues, so failed suspension does not guarantee that
  the game stops consuming resources.
- Test physical card-reader sign-in, GPU/audio hardware and Wi-Fi adapters.
  WPA3 and captive portals need device-level checks. Existing card behavior is
  preserved; the latest graphical proof uses a managed temporary test identity.
- Avatar display remains optional visual polish.

## Validation

The package check runs headless Textual tests for browse, search, temporary
play, install, navigation, overlay dismissal, and actionable failure states,
plus transport pagination tests. Hyprland's real configuration verifier and
the existing Lua workspace tests validate configuration integration.

Build with `nix build .#kwak-kiosk .#checks.x86_64-linux.desktop-config
.#checks.x86_64-linux.window-modes`. The store now labels saved additions Add to My Games. The NixOS userd VM
test checks identity signing and a real user-service freezer/thaw cycle.
Native library reconciliation is owned and tested by the pinned kwakore dependency.

Validated on 2026-10-11: 171 app tests, flake evaluation, compositor config
verification, workspace tests, and all 11 NixOS lifecycle subtests passed. The
VM test ran through its built driver with host-user KVM access after the
sandboxed runner fell back to slow emulation. The bunker fixture uses a stable
preauthorized client because nak 0.17's constant connect secret alone does not
authorize subsequent signing. [Validation receipt](proofs/kiosk-integration.json).

Follow-up validation: 13 kiosk tests, 23 settings tests and 121 account-service
tests pass. The latter includes actual local Nak NIP-46 login/signing combined
with NIP-42 relay authentication and library read/write. Nak is a required Nix
test dependency so this integration cannot be silently skipped in package builds.
All three application packages build; flake evaluation and compositor config
checks pass. The expanded lifecycle VM passes all 11 subtests, including both
the runtime and a spawned game-process counter freezing and resuming.
[Session suspension receipt](proofs/session-suspension.json).

The earlier disposable graphical VM passed seven checks through actual QEMU key input:
clean configuration/plugin loading, lone Windows key, transparent background
blending, Escape/focus restoration, Super+Q without a stray overlay, Hyprflow
selection, and Settings keyboard launch/exit. Its game window is a terminal
fixture, and its account-library error is expected because this administrator
session has no Nostr account service. The screenshot is a real compositor capture.
[Desktop receipt](proofs/kiosk-desktop/runtime.json).

![Earlier library-style overlay above a terminal game fixture](proofs/kiosk-desktop/overlay.png)

![Network settings in the disposable VM](proofs/kiosk-desktop/network.png)

Live NetworkController activation additionally passed against a simulated WPA2
access point with DHCP, running as an ordinary user. The profile remained
unsaved; no secret remained in NetworkManager files after activation. All
radios, profile, services, firewall exceptions and fixture policy were cleaned
up. [Wi-Fi receipt](proofs/network-wifi.json).

Real PipeWire/WirePlumber commands also passed volume parsing, five-percent
changes, the 100% cap and mute/unmute against a temporary null sink in the VM.
The sink and default selection were restored afterward; physical audio remains
a hardware check. [Audio receipt](proofs/audio-controls.json).

Reproduce the graphical checks with a fresh disposable VM disk, test-only root
SSH key/port, autologin as `kwak`, QMP socket, and Pillow on the host:

```sh
python3 tests/kiosk-desktop.py --qmp /path/to/owned-vm/qmp.sock \
  --ssh-port 22943 --identity /path/to/test-key --output /tmp/kiosk-proof
```

The harness checks the VM hostname/name and requires an otherwise empty desktop.
It sends input only through the supplied QMP socket, captures via guest `grim`,
and terminates only its own fixture windows. It does not deploy the OS or touch
the user's running VM.

## Dedicated game overlay validation

The latest kiosk package passes 40 tests, and the account service passes 136.
Both packages build, flake evaluation passes, and the real compositor
configuration and workspace checks pass.

The managed-account graphical VM passes nine checks with real WebKit games:
Play Now without installation, Windows-key overlay over a fullscreen game,
capture excluding the overlay, cancellation without a post, confirmed signed
Blossom upload and Nostr publication, exact game focus restoration, library on
workspace one, Hyprflow selection, and closing only the chosen trial while two
other games remain running. [Overlay receipt](proofs/quack-overlay/runtime.json).

![Dedicated game menu above a fullscreen WebKit napplet](proofs/quack-overlay/game-menu-fullscreen.png)

The reproducible [overlay harness](../tests/kiosk-overlay-e2e.py) runs while the
[managed-session harness](../tests/kiosk-session-e2e.py) holds its game-ready
checkpoint. The fixture uses only a local TLS relay and Blossom server; no
publication reaches a public relay.

The real managed-session run also validates greetd/PAM sign-in, automatic
NIP-78 restoration and two independent WebKit instances. Idle switching freezes
the game service: both heartbeat counters and cgroup CPU accounting remain
unchanged during the two-second check. Injecting the same card through the
actual card-service identity resumes the original processes and game instances.
Explicit sign-out deletes the temporary account, home and remaining UID
processes. [Session receipt](proofs/kiosk-real-games/idle-session.json).

A second clean run repeats those checks using **Switch Account** from an
actual systemd user-manager process. This verifies the narrow UWSM fallback
when apps lack a login-session ancestor; callers still need to own the visible
session. [Switch Account receipt](proofs/kiosk-real-games/switch-account.json).

## Settings inside the game center

Choose **Settings** in the launcher to open a full-screen view in the same TUI.
The category rail on the left contains Game center, Sound, Network, Display &
windows, Profile, Relays, and Media servers. Select a category with the mouse
or arrow keys and Enter; **Ctrl+Left** focuses the category rail. **Escape** or
**Close** returns to your game list with its search and selection preserved.

Game center checks the real kwakore service and counts installed games
automatically, including after temporary service failures. Sound uses PipeWire, Wi-Fi uses
NetworkManager, and window modes use the active Hyprland session. Profile,
relay, and Blossom changes use the kiosk account service and its existing
publish flow. These controls report unavailable services when run on a host
without the corresponding desktop or kiosk account service. Local mode uses
the current user's kwakore library and does not enable kiosk account sync.

Run locally with `nix run .#kwak-kiosk -- --local`. On the destination kwakOS
system, run `kwak-kiosk` after rebuilding the system with this source. The
standalone `kwak-settings` command shares the same category view and controls.
The game runtime remains in kwakore; this UI does not change its daemon
configuration or expose unsupported runtime options.

## Discovery cards

Find Games uses a responsive card grid with a compact shell and a dedicated game details
page. Click a card or press Enter to open its dedicated Napplet details page.
Use arrows (or Home/End) to move through the grid. Play and Install are explicit
actions on the details page. Press Enter in search to return to the cards.
Install adds the game to the local or synced library. My Games retains its
library list and game management controls.

Cards load public HTTPS artwork lazily as they enter the viewport, using the
bounded image cache. Missing artwork gets a title-based fallback icon; broken
artwork shows an availability placeholder. The current kwakore CLI catalog
omits artwork URLs, so real catalog cards use fallback icons until the daemon
provides image/icon metadata. The UI accepts image, icon, banner, picture,
icon_url, or image_url fields without independently querying Nostr relays.

## Launcher style and interaction contract

The user approved this direction for future launcher work: a TUI game center
inspired by Steam desktop and Big Picture, with discovery as its main visual
focus and kwakore as the background game service. Preserve these conventions
unless the user asks to change them:

- **Compact shell:** prominent three-row primary navigation buttons; no large branding title
  or outer gutters. Spend space on browsing and useful game details rather
  than decorative framing or repeated headings.
- **Cover grids:** My Games and Find Games use responsive artwork cards with
  a name, creator, and short availability status across the browsing width. Open a
  dedicated store/details page with artwork, creator, description, release
  information, and clear Play and Install controls. Keep library
  management controls in My Games.
- **Visual consistency:** use the shared black-and-green kwak theme. Use
  restrained borders, clear hover feedback, and an unmistakable selected and
  focused card. Reuse the existing widgets and theme rather than introducing
  a second visual system.
- **Mouse and keyboard parity:** a click or Enter opens card details without
  launching the game; arrows and Home/End navigate. Enter from search focuses
  the cards. Play and Install require explicit actions on the details page. Keep labels readable and actions reachable at
  small terminal sizes, including 80×28 and the kiosk's 88×33 layout.
- **Automatic freshness:** do not add routine Refresh, Scan, or Check updates
  controls. Read data quietly on tab entry and at background intervals. Keep
  existing data visible during reads, preserve focus and edits, and retry
  temporary failures automatically. User actions should express intent (Play,
  Install, Update, Connect, Publish), not daemon maintenance.
- **Continuity:** settings opens inside the main TUI with categories on the
  left. Escape returns to browsing. Preserve search, selection, and scroll
  position across updates and when returning from settings.
- **Artwork behavior:** reuse the bounded image cache, load artwork as cards
  enter the viewport, and keep missing or failed artwork understandable.
  Prefer real Napplet artwork supplied by kwakore; fallback icons support
  incomplete metadata rather than replacing the intended artwork.
- **Service boundary:** discovery, installation, launch, and game management
  remain backed by kwakore. Share controls between local and destination
  kiosk use; expose unavailable services clearly.

For changes to browsing or navigation, check mouse and keyboard flows,
selection preservation, responsive card layout, and readable action labels.
Use the kiosk tests and local smoke runner; document any remaining daemon
metadata limitations rather than implying that unsupported features work.

## Automatic background updates

The visible library or discovery tab updates every 60 seconds and when opened.
Game windows are checked every three seconds; account library sync retains its
30-second interval. Release availability is checked on library entry and every
five minutes; the Update action appears only when a new release is available.
Installing a release still requires the user's explicit Update action.

Settings reads the active category on entry and every 15 seconds. Wi-Fi scans
preserve the selected network and typed password. Audio reads preserve focus;
account forms update unchanged fields while retaining edits. Background reads keep existing games and
cards visible, reuse unchanged cards, and do not show routine loading messages
or disable controls. Slow requests are skipped while their worker is active.
Temporary catalog failures retain the current view and retry automatically.

## Dedicated Napplet details pages

The primary navigation uses larger buttons. My Games and Find Games have
separate search inputs and retain their own queries when switching tabs.
Discovery's search area is reserved for future catalog filters.

Clicking a discovery card or pressing Enter opens a dedicated store page with
artwork, creator, description, release information, and required capabilities
when provided by kwakore. Play Now tries an uninstalled game; Install adds it
to the library. Installed games offer Play, and Stop, Update, or Remove from
My Games appear when applicable. Escape or Back returns to the same discovery
query, selection, and scroll position. Game actions remain backed by the shared
launcher runtime; merely opening details never installs or launches a game.

## Big Picture library hierarchy

My Games uses a responsive cover grid. Clicking a cover or pressing Enter opens
its dedicated game page; selecting or opening details never launches a game.
Back or Escape returns to the same query, selected cover, and scroll position.
Find Games uses the same browsing model with its own search query.

The game page places artwork, title, creator, and current state above explicit
Play/Stop, Update, and Install actions. The primary button changes from Play
to Stop while this game is running. Applicable actions remain near the top
while information below scrolls independently. Game Info and Releases separate
the description and capabilities from installed/available release information.
Background refreshes preserve the active information tab.

This adopts the references' visual hierarchy without inventing Steam-specific
data. Play time, last played, achievements, activity, and friends should appear
only when kwakore exposes meaningful data for them. Version pinning still
requires the corresponding core API; release history remains read-only.

## Explicit build checks

Normal package builds and launches skip the application test suites. Run
`nix build .#checks.x86_64-linux.kwak-kiosk` to run the kiosk suite, or
`nix flake check` for all flake checks. CI should select these check targets
explicitly; launching `nix run .#kwak-kiosk -- --local` does not invoke them.
