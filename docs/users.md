# Nostr users

Every person who signs in to kwakOS with a Nostr key gets their own Unix user.
The username is `n` followed by the first 10 hex characters of their public key,
for example `n3bf0c63fcb`. Installed systems and the VM use this sign-in; the live
ISO logs straight into `kwak` instead (`kwak.nostrUsers.enable = false`).

## Signing in

The sign-in screen (`kwak-greeter`) is a terminal app in a full-screen kitty
window, run in `cage` by greetd. Use the mouse, or the arrow keys, Tab, Enter, and
Esc to go back. It lists every account on the computer.

There are two kinds of account:
- **Kept accounts** stay on the computer and have a Unix password. That password
  signs them in, switches to their session, and unlocks their screen. A swipe of
  the account's card does the same.
- **Guests** have no password. A guest and all its files are deleted when it logs
  out. Guests are only listed while they exist.

Choosing an account from the list:

| Account | Choosing it |
| --- | --- |
| **Password** or **Remote signer** (kept) | Asks for the account's password, or a swipe of its card. |
| **Guest** | Starts the session straight away. |

Below the list, **Sign in with another account…** offers the ways to set up an
account. Each has a **Keep this account on this computer** box. It starts off, which
makes a guest. Checking it asks for a password, typed twice, and keeps the account.

| Option | What happens |
| --- | --- |
| **New account** | Generates a new key and starts the session. A kept account stores the key as an ncryptsec (NIP-49) under its password. |
| **Existing account** | Paste an nsec or ncryptsec. A kept nsec is stored as an ncryptsec under the chosen password. An ncryptsec always asks for its own password; kept, it is stored as given and that password becomes the account's password. A guest holds the key in memory only. |
| **Remote signer** | Paste a bunker:// URI. The signer must sign a fresh challenge while the loading screen shows. The user is created for the pubkey that signed it. The URI is stored, for a guest too, until the account is deleted, because signing goes through it. |
| **Linux user** | A normal username and password, such as the `kwak` administrator. |

The rules for keys that are already on the computer:
- **A key or bunker of a kept account** isn't enough to sign in to it. The greeter
  then asks for the account's password (or a swipe of its card).
- **Keeping a guest**, by signing in with its key and the keep box checked, gives it
  a password and stores its key.

For nsec and ncryptsec accounts, the Unix password and the ncryptsec password are
the same. Unlocking with it also decrypts the key, so signing keeps working.
Changing it with `passwd` would leave the stored ncryptsec under the old password,
and signing would stop until you sign in again.

Plaintext secret keys are never written to disk. Passwords and keys are passed to
`nak` on stdin or in its environment, never as command-line arguments.

## Swipe cards

With an MSR90 magnetic card reader plugged in, you can also sign in by swiping a
[Nostr swipe card](https://relay.ngit.dev/npub1ye5ptcxfyyxl5vjvdjar2ua3f0hynkjzpx552mu5snj3qmx5pzjscpknpr/nostr-swipe-cards.git)
at the sign-in screen. The list then says **Or swipe your card to sign in.** A card
works like a pasted key:

| Card | Swiping it |
| --- | --- |
| **SKC1** (a plain secret key) of an account on the computer | Signs in straight away. The swipe authenticates it, so a kept account needs no password. |
| **SKC1** not on the computer yet | Offers the keep box, like **New account**: a guest, or kept with a password. |
| **SKC2** (an ncryptsec) | Asks for the card's password, which is needed to decrypt the key. A new card offers the keep box; kept, the card's password becomes the account's password. |
| **SKC3** (an nbunksec bunker connection) of an account on the computer | Shows the loading screen while the card's remote signer signs the login challenge. No password. |
| **SKC3** not on the computer yet | Offers the keep box, then connects to the card's signer, like **Remote signer**. The signer must already have paired the card's client key (the `skc tui` write flow pairs it). The bunker connection and client key are stored. |

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
| Already signed in | The current session locks, and the screen switches to that account's session and unlocks it. The card authenticates the account, so no password is asked. Swiping your own card unlocks your locked session. |
| Anything else | The current session locks, and a **Switch account** sign-in screen opens on the next free virtual terminal to handle the swipe, as at the normal sign-in screen: it offers a new card the keep box, asks for an SKC2 card's password, or connects to an SKC3 card's signer. The new session then runs there. Esc on that screen goes back to the locked session. |

A card is recognised as an account's from its key (SKC1), from the ncryptsec stored
when it first signed in (SKC2: the same card, not just the same key), or from the
stored signer and client key (SKC3). So once an SKC2 card has signed in, the card
alone reopens that account's running session; keep it as safe as an SKC1 card.

Signing in to an account that already has a session, from any sign-in screen,
switches to that session instead of starting a second one. From the list, a kept
account's session needs its password (or its card), so one person can't switch
into another's session.

**The lock screen** is hyprlock, run by hypridle when a session is locked (and
before suspend). Unlock it with:
- **A kept account:** its password, or a swipe of its card.
- **A guest:** Enter.
- **A local account** such as `kwak`: its Unix password.

Guest sessions are not locked when switching away, since a guest has no password
and anyone could open it from the account list anyway.

When a switched-to session logs out, its sign-in screen closes and the screen goes
back to another session (locked), or to the sign-in screen on the first virtual
terminal.

## Lock, switch account, or log out

**Lock, Switch Account or Log Out** in the launcher (or Super+Shift+M, or
`kwak-session`) opens the session menu:

| Option | What it does |
| --- | --- |
| **Lock** | Locks the screen (also Super+L). Unlock with your password or card. Not offered to guests, who have no password. |
| **Switch account** | Locks this session and opens the **Switch account** sign-in screen. Your session keeps running, so you can go back to it with your password or card. Esc on that screen returns to it straight away. |
| **Log out** | Asks first, then ends the desktop session and returns to the sign-in screen. A kept account stays on the computer. For a guest, logging out deletes the account. |
| **Sign out of this computer** | Deletes a kept account (see below). |

Local accounts such as `kwak` get **Lock**, **Switch account** and **Log out**.

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
  - A Nostr user may only sign themselves out, check their own password to
    unlock their locked session (hyprlock's PAM stack runs `kwak-userd pam-unlock`),
    and lock their own session to open a switch greeter (`switch_account`, which
    local accounts may also use). Their kwakore daemon may also sign as them (see
    [Napplets](#napplets)).
  - The `kwak-cards` reader service may only pass on card swipes.
- **Sign-in** gives the greeter a single-use login token, valid for 60 seconds. The
  greeter gives the token to greetd as the password, and `pam_exec` checks it with
  `kwak-userd`. Local accounts fall through to the normal password check.
- **Switching** uses logind: kwak-userd locks, activates, and unlocks sessions with
  `loginctl`, and opens a switch greeter as `kwak-greeter-switch@ID.service`, a
  greetd instance on the next free VT whose greeter runs `kwak-greeter --switch ID`.
  Only the greeter whose session is on screen gets swipes.
- **Accounts** get UIDs from 30000–39999 and the `nostr` group. A kept account's
  Unix password is set with `chpasswd`. kwak-userd checks it through the
  `kwak-userd` PAM service, which uses pam_unix only. A guest's password stays
  locked. sshd denies the `nostr` group, so these accounts can't log in over SSH.
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
