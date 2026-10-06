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
crashes first, `kwak-userd-cleanup.service` removes them at the next boot.

## How it works

- `kwak-userd` is a root service on `/run/kwak-userd.sock` and knows who is calling
  from the caller's Unix UID:
  - The `greeter` user may sign in or create identities.
  - Root may redeem login tokens and remove users.
  - A Nostr user may only sign themselves out.
- **Sign-in** gives the greeter a single-use login token, valid for 60 seconds. The
  greeter gives the token to greetd as the password, and `pam_exec` checks it with
  `kwak-userd`. Local accounts fall through to the normal password check.
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
nix develop ./apps/userd   # Python with pynacl, plus nak
```

`nix build .#checks.x86_64-linux.userd` boots a NixOS VM with a local relay and
runs each sign-in and sign-out flow end to end. Without KVM it is very slow.
