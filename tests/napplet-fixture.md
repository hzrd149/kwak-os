# Real napplet kiosk fixture

Use only inside a disposable VM. This serves three real signed NIP-5D napplets,
a NIP-01 TLS relay, hash-addressed HTML blobs, and an HTTPS Blossom upload
endpoint. Kwakore performs its normal
discovery, download, verification, sandbox creation and WebKit execution.

Requires Python with `websockets`, `nak`, and `openssl`. Prepare and serve:

```sh
python3 tests/napplet-fixture.py prepare --directory /root/kwak-fixture --saved \
  --nak /run/current-system/sw/bin/nak \
  --tls-cert /root/kwak-fixture/tls-cert.pem \
  --tls-key /root/kwak-fixture/tls-key.pem
python3 tests/napplet-fixture.py serve --directory /root/kwak-fixture \
  --nak /run/current-system/sw/bin/nak
```

Without certificate arguments, preparation generates a short-lived test
certificate. Trust that certificate in the VM's system CA store before starting
kwakore and kwak-userd. Kwakore requires `wss://` relays; disable external
discovery and explicitly configure the local Blossom server:

```json
{
  "relays": ["wss://localhost:18443"],
  "blossom_servers": ["http://127.0.0.1:18080"],
  "discover_on_user_relays": false,
  "signer": {"mode": "system", "socket": "/run/kwak-userd.sock"}
}
```

The publisher uses the public test key `33` repeated 32 times. The player uses
the public test key `11` repeated 32 times. Preparation seeds the player's name,
relay list and encrypted native kind-30078 `kwak-library` list. `--saved` includes
`kiosk-heartbeat` and `kiosk-second`; `kiosk-trial` remains absent so Play Now can
be checked without saved-list or installed-record changes. Omit `--saved` to
exercise empty-library recommendations. The relay also accepts signed updates
and persists them in `events.json`.

Preparation also seeds kind 10063 with `https://localhost:18444`. That server
accepts `PUT /upload` with a signed Blossom kind-24242 authorization, checks its
signature, expiration, upload operation and PNG hash, then returns the URL,
size, hash and media type. Uploaded screenshots are retrievable from the same
HTTPS origin. The HTTP endpoint on port 18080 continues serving game artifacts.
All publication stays in these local fixtures. When running the Nix Python
environment on the host with a custom trust file, set both `SSL_CERT_FILE` and
`NIX_SSL_CERT_FILE`; the disposable VM can use its configured system CA store.
Use the absolute Nak path when launching through `systemd-run`, whose service
environment may not include the interactive shell's executable search path.

`metadata.json` gives each game's canonical address, blob hash, aggregate
artifact hash and exact shared NAP storage filename. In the user's kwakore data
directory, read `napplet-storage/<storage_filename>`, select `kiosk.fixture`,
and parse its JSON string. The payload includes:

- `instance`: stable identity of that running WebKit frame.
- `heartbeat`: advances every 250 ms, including when unfocused.
- `gameTicks`: advances while focused; the fixture pauses its own gameplay on blur.
- `score`: increments after clicking the large Play button or activating it with Enter.
- `focused`: the frame's focus state.
- `audio`: Web Audio context state after Play starts a quiet oscillator.
- `errors`: storage/audio failures.

A frozen service should stop `heartbeat`; thawing should resume it while keeping
the same `instance` and score. Overlay focus loss should stop `gameTicks` and
suspend audio while the heartbeat continues. After returning, clicks and audio
should recover. The fixture voluntarily pauses on blur; that result does not
prove every third-party game behaves identically. Web Audio state is telemetry,
so separately inspect the real PipeWire stream/output when validating audio.

## Live overlay checks

The session harness `tests/kiosk-session-e2e.py --checkpoint` can pause after
the real player's two saved games are running. It writes `game-ready.json`
containing the user environment and protected game PIDs. Leave that checkpoint
paused while exercising the overlay, and point the player's test-only profile
links at the new kiosk package so the actual Windows binding launches it.

From the host, run:

```sh
python3 tests/kiosk-overlay-e2e.py \
  --qmp /path/to/owned-vm/qmp.sock \
  --ssh-port 22944 --identity /path/to/test-ssh-key \
  --checkpoint-dir /path/to/session-proof \
  --kiosk-store /nix/store/REPLACE-kwak-kiosk-0.1.0 \
  --output /tmp/kwak-overlay-proof
```

The harness verifies the dedicated menu above a fullscreen real trial game,
game-only screenshot capture, canceled and confirmed local screenshot
publication, Resume, Back to Library on workspace 1, Hyprflow, and Exit Game.
It protects the checkpoint's saved game PIDs and closes only its own trial,
including on failure. The PNG comparison rejects overlay pixels in the saved
image. Successful checks and the exact tested package are recorded in
`runtime.json`, with compositor screenshots beside it. After the harness
finishes, resume the session checkpoint to test account switching and cleanup.

Fixture preparation uses the kwak-userd Python environment for coincurve and cryptography.
