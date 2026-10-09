# Nostr users

Every person who signs in to kwakOS with a Nostr key gets their own Unix user.
The username is `n` followed by the first 10 hex characters of their public key,
for example `n3bf0c63fcb`. Installed systems and the VM use this sign-in; the live
ISO logs straight into `kwak` instead (`kwak.nostrUsers.enable = false`).

## Signing in

The sign-in screen (`kwak-greeter`) is a terminal app in a full-screen kitty
window, run in `cage` by greetd. Use the mouse, or the arrow keys, Tab, Enter, and
Esc to go back. It lists every account on the computer. Choosing one signs in to it:

| Account | Choosing it |
| --- | --- |
| **Password** (an ncryptsec) | Asks for the password and decrypts the key. |
| **Remote signer** (a bunker) | Shows a loading screen until the signer approves the login. Cancel goes back. |
| **Guest** (an nsec without a password) | Starts the session straight away. Guests are only listed while they exist, until they log out. |

Below the list, **Sign in with another account…** offers:

| Option | What happens |
| --- | --- |
| **New account** | Asks for an optional password, then generates a new key and starts the session. With a password the key is kept as an ncryptsec; without one the account is a guest, deleted with its key at logout. |
| **Existing account** | Paste an nsec or ncryptsec. An nsec with a password is kept as an ncryptsec (NIP-49) protected by that password; an nsec without one signs in as a guest. An ncryptsec is checked with its password and kept as given. |
| **Remote signer** | Paste a bunker:// URI. The signer must sign a fresh challenge while the loading screen shows. The user is created for the pubkey that signed it, and the URI is kept. |
| **Linux user** | A normal username and password, such as the `kwak` administrator. |

The rules for keys that are already on the computer:
- **Adding a password** to a guest, by signing in with the nsec and a password, makes it saved.
- **Signing in with the nsec and no password** to a saved identity keeps it saved and leaves its stored key alone.
- **Signing in with the nsec and a new password** replaces the old password.

Plaintext secret keys are never written to disk. Passwords and keys are passed to
`nak` on stdin or in its environment, never as command-line arguments.

## Swipe cards

With an MSR90 magnetic card reader plugged in, you can also sign in by swiping a
[Nostr swipe card](https://relay.ngit.dev/npub1ye5ptcxfyyxl5vjvdjar2ua3f0hynkjzpx552mu5snj3qmx5pzjscpknpr/nostr-swipe-cards.git)
at the sign-in screen. The list then says **Or swipe your card to sign in.** A card
works like a pasted key:

| Card | Swiping it |
| --- | --- |
| **SKC1** (a plain secret key) of an account on the computer | Signs in straight away. A saved account keeps its stored key and password. |
| **SKC1** not on the computer yet | Asks for an optional password, like **New account**. With one, the key is kept as an ncryptsec; without one, the account is a guest. |
| **SKC2** (an ncryptsec) | Asks for the card's password. The ncryptsec is kept, so later the password alone signs in, from the list. |
| **SKC3** (an nbunksec bunker connection) | Shows the loading screen while the card's remote signer signs the login challenge, like **Remote signer**. The signer must already have paired the card's client key (the `skc tui` write flow pairs it). The bunker connection and client key are kept, so the account can then be chosen from the list. |

- **Swiping during a session switches accounts** (see below). Swipes are never
  typed into a window, because the reader's keyboard output stays switched off
  while the reader service runs.
- **An SKC1 card is a bearer key.** Anyone who swipes it can sign in as you, so
  keep it like a house key. An SKC2 card also needs its password. An SKC3 card can
  ask your signer to sign for you, so revoke its client key in the signer if you
  lose it.
- **Unreadable or unknown cards** say "That card couldn't be read."

When the reader is plugged in, udev starts `kwak-card-reader@hidrawN.service`. It
runs `kwak-cards` as the `kwak-cards` system user, which is the only user that can
read the reader. It stops when the reader is unplugged. To use `skc` from a session
(for example to write cards), stop the service first; to turn card sign-in off, set
`kwak.nostrUsers.cards.enable = false`.

kwak-userd keeps a swipe for 2 minutes and hands it only to the sign-in screen that
is on screen. The greeter never sees the card's key: it gets a summary and an opaque
id, and signs in with `card_login`.

## Switching accounts

Several people can be signed in at once, each in their own session on its own
virtual terminal. Swiping a card anywhere (in a session, on the lock screen, or at a
sign-in screen) switches to that card's account:

| The card's account | What happens |
| --- | --- |
| Already signed in | The current session locks, and the screen switches to that account's session and unlocks it, with no password or signer. Swiping your own card unlocks your locked session. |
| Anything else | The current session locks, and a **Switch account** sign-in screen opens on the next free virtual terminal to handle the swipe, as at the normal sign-in screen: it asks for an SKC2 card's password, or connects to an SKC3 card's signer, creating the user if needed. The new session then runs there. Esc on that screen goes back to the locked session. |

A card is recognised as an account's from its key (SKC1), from the ncryptsec stored
when it first signed in (SKC2: the same card, not just the same key), or from the
stored signer and client key (SKC3). So once an SKC2 card has signed in, the card
alone reopens that account's running session; keep it as safe as an SKC1 card.

Signing in to an account that already has a session, from any sign-in screen,
switches to that session instead of starting a second one.

**The lock screen** is hyprlock, run by hypridle when a session is locked (and
before suspend). Unlock it with:
- **A password account:** its password.
- **A remote signer account:** press Enter with no password and approve on the
  signer.
- **Any card account:** a swipe of its card.
- **A local account** such as `kwak`: its Unix password.

Guest sessions are not locked when switching away, since a guest has no password
and anyone could open it from the account list anyway.

When a switched-to session logs out, its sign-in screen closes and the screen goes
back to another session (locked), or to the sign-in screen on the first virtual
terminal.

## Signing out

Logging out of a saved identity keeps the user and their home folder. To remove it,
open **Sign Out of This Computer** from the launcher (or run `kwak-userd signout`).
After you confirm, a separate job:
- ends the session;
- deletes the Unix user and their home folder;
- deletes their saved key, avatar, and files left in `/tmp`, `/var/tmp`, and `/dev/shm`;
- deletes their journal, cron, mail, and Nix profile files.

Your Nostr identity itself is not affected.

Temporary users are removed the same way when their session closes. If the machine
crashes first, `kwak-userd-cleanup.service` removes them at the next boot. It also
finishes a saved account's deletion if sign-out was interrupted by a restart.

## Napplets

Every Nostr user gets [kwakore](https://github.com/hzrd149/kwakore), which runs
napplets in their own windows. It is a user service, started the first time it is
used (the `kwakore` command or a napplet's launcher entry). Napplets are already
signed in as you: kwakore's `system` signer asks `kwak-userd` to sign, so you never
give kwakore your key.

- After you sign in, kwak-userd keeps your secret key **in memory only**, until you
  log out or sign out. It is never written down unencrypted. Bunker identities keep
  using the bunker, which approves each signature.
- Only kwakore's daemon may ask: kwak-userd checks the calling program
  (`/proc/PID/exe` against `kwak.nostrUsers.signerClients`) as well as the user.
  kwakore asks you in the napplet's window before it signs, encrypts, or decrypts.
- If kwak-userd restarts during a session, it no longer holds your key. Lock the
  screen and unlock it with your password, and signing works again.
- Bunker identities can sign but cannot encrypt or decrypt yet.

`kwakore signer status` shows the signer. `kwakore signer switch none` signs napplets
out for good, and `kwakore signer switch system` signs them back in.

To give each newly created account a starting set of napplets, configure their
addresses in Nix:

```nix
kwak.nostrUsers.defaultNapplets = [
  "nostr:naddr1..."
];
```

The list defaults to empty. Account creation copies it into the new home; a
user service installs those napplets when the account first signs in. Failed
installs retry at a later sign-in. Once a default installs, the user can remove
it without it returning. Changing the Nix list affects newly created accounts,
not existing ones.

## How it works

- `kwak-userd` is a root service on `/run/kwak-userd.sock` and knows who is calling
  from the caller's Unix UID:
  - The `greeter` user may sign in or create identities.
  - Root may redeem login tokens and remove users.
  - A Nostr user may only sign themselves out, and check their own password or
    signer to unlock their locked session (hyprlock's PAM stack runs
    `kwak-userd pam-unlock`). Their kwakore daemon may also sign as them (see
    [Napplets](#napplets)).
  - The `kwak-cards` reader service may only pass on card swipes.
- **Sign-in** gives the greeter a single-use login token, valid for 60 seconds. The
  greeter gives the token to greetd as the password, and `pam_exec` checks it with
  `kwak-userd`. Local accounts fall through to the normal password check.
- **Switching** uses logind: kwak-userd locks, activates, and unlocks sessions with
  `loginctl`, and opens a switch greeter as `kwak-greeter-switch@ID.service`, a
  greetd instance on the next free VT whose greeter runs `kwak-greeter --switch ID`.
  Only the greeter whose session is on screen gets swipes.
- **Accounts** get UIDs from 30000–39999, the `nostr` group, and a locked Unix
  password, so they cannot log in over SSH.
- **The registry** in `/var/lib/kwak-userd/users.json` binds each username to its
  full public key. A different key with the same 10-character prefix is refused.
  Only users in the registry and in that UID range can be removed.
- **New users** get their kind 0 profile and kind 10002 relay list from
  `kwak.nostrUsers.relays`. The display name becomes the account's full name, and
  the avatar appears on the sign-in screen. Setup hooks (see below)
  then run as root.

## Default home folder

Programs and desktop defaults are system-wide: `environment.systemPackages`, and app
configs in `/etc/xdg`. Rebuilding updates them for every user, and anything in a
user's own `~/.config` takes precedence. Two options set up a new user's home:

```nix
kwak.nostrUsers = {
  # Copied once into each new home; the user owns and can edit the copies.
  homeFiles.".config/kitty/kitty.conf" = ./kitty.conf;
  # Run as root, in name order, with KWAK_USER, KWAK_PUBKEY, and KWAK_HOME set.
  setupHooks."50-welcome" = ''echo "Welcome" > "$KWAK_HOME/welcome.txt"'';
};
```

Both run only when the user is created; later rebuilds don't change existing homes.

Administration, as root:

```sh
kwak-userd list           # saved identities
kwak-userd remove NAME    # delete one, like signing out
kwak-userd cleanup        # remove leftover temporary identities
```

## Develop and test

Each app is its own flake under `apps/`:

```sh
nix build ./apps/userd     # runs the user manager's unit tests
nix build ./apps/greeter   # runs the greetd protocol and headless UI tests
nix build ./apps/cards     # runs the card reader tests and skc_cards' own tests
nix flake update nostr-swipe-cards   # update the swipe card library
nix develop ./apps/userd   # Python with pynacl, plus nak
```

`nix build .#checks.x86_64-linux.userd` boots a NixOS VM with a local relay and
runs each sign-in and sign-out flow end to end. Without KVM it is very slow.
