# Nostr users

Every person who signs in to kwakOS with a Nostr key gets their own Unix user.
The username is `n` followed by the first 10 hex characters of their public key,
for example `n3bf0c63fcb`. Installed systems and the VM use this sign-in; the live
ISO logs straight into `kwak` instead (`kwak.nostrUsers.enable = false`).

## Signing in

The sign-in screen (`kwak-greeter`, running in `cage` under greetd) lists the saved
identities and offers three more options:

| Sign in with | What happens | Next time |
| --- | --- | --- |
| **nsec + password** | The user is created and the key is kept, encrypted as an ncryptsec (NIP-49) with that password. | Choose your name and enter the password. |
| **nsec, no password** | A **temporary** user is created and nothing is saved. Logging out deletes the user and all of their files. | Paste the nsec again. |
| **ncryptsec + its password** | The key is decrypted to check the password, then the user is created and the ncryptsec is kept as given. | Choose your name and enter the password. |
| **bunker:// URI** | The remote signer must sign a fresh challenge. The user is created for the pubkey that signed it, and the URI is kept. | Choose your name and approve the request in your signer. |
| **Create a new identity** | A new key is generated and shown once for backup. With a password it is kept as an ncryptsec; without one the user is temporary. | As for nsec. |
| **Local account…** | A normal username and password, such as the `kwak` administrator. | |

The rules for keys that are already on the computer:
- **Adding a password** to a temporary identity, by signing in with the nsec and a password, makes it saved.
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
nix build ./apps/greeter   # runs the greetd protocol tests
nix develop ./apps/userd   # Python with pynacl, plus nak
```

`nix build .#checks.x86_64-linux.userd` boots a NixOS VM with a local relay and
runs each sign-in and sign-out flow end to end. Without KVM it is very slow.
