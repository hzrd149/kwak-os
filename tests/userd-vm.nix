# End-to-end Nostr identity lifecycle against a local `nak serve` relay.
{ pkgs, usersModule }:
let
  relay = "ws://127.0.0.1:10547";
  # Talks to kwak-userd as whichever user runs it: client OP [JSON fields].
  client = pkgs.writeScript "kwak-userd-client" ''
    #!${pkgs.python3}/bin/python3
    import json, socket, sys
    s = socket.socket(socket.AF_UNIX)
    s.connect("/run/kwak-userd.sock")
    s.sendall(json.dumps({"op": sys.argv[1], **json.loads(sys.argv[2] if len(sys.argv) > 2 else "{}")}).encode() + b"\n")
    reply = json.loads(s.makefile().readline())
    if not reply["ok"]:
        sys.exit(reply["error"])
    print(json.dumps(reply["result"]))
  '';
in
pkgs.testers.runNixOSTest {
  name = "kwak-userd";
  nodes.machine = {
    imports = [ usersModule ];
    kwak.nostrUsers = {
      relays = [ relay ];
      homeFiles.".config/kitty/kitty.conf" = pkgs.writeText "kitty.conf" "font_size 12\n";
      setupHooks."50-welcome" = ''echo "$KWAK_PUBKEY" > "$KWAK_HOME/welcome.txt"'';
    };
    # Keep greetd's PAM service but skip the graphical greeter.
    systemd.services.greetd.enable = false;
    systemd.services.nak-relay = {
      wantedBy = [ "multi-user.target" ];
      serviceConfig.ExecStart = "${pkgs.nak}/bin/nak serve --hostname 127.0.0.1";
    };
    environment.systemPackages = [ pkgs.pamtester ];
  };

  testScript = ''
    import json, shlex

    def call(op, as_user="greeter", **fields):
        out = machine.succeed(f"runuser -u {as_user} -- ${client} {op} {shlex.quote(json.dumps(fields))}")
        return json.loads(out)

    def pam(user, token, *steps):
        return machine.execute(
            f"echo {token} | pamtester greetd {user} authenticate acct_mgmt {' '.join(steps)}"
        )[0]

    def new_key():
        sk = machine.succeed("nak key generate").strip()
        pk = machine.succeed(f"echo {sk} | nak key public").strip()
        return sk, pk, "n" + pk[:10]

    def assert_gone(user, uid):
        machine.wait_until_fails(f"getent passwd {user}", timeout=120)
        machine.succeed(f"test ! -e /home/{user}")
        machine.succeed(f"test ! -e /var/lib/kwak-userd/keys/{user}")
        machine.fail(f"pgrep -U {uid}")
        assert machine.succeed(f"find / -xdev -uid {uid}").strip() == ""
        assert user not in machine.succeed("cat /var/lib/kwak-userd/users.json")

    machine.wait_for_unit("sockets.target")
    machine.wait_for_unit("nak-relay.service")
    machine.wait_for_open_port(10547)

    with subtest("nsec with a password is saved as an ncryptsec"):
        sk, pk, user = new_key()
        grant = call("login_nsec", nsec=sk, password="hunter2")
        assert grant["username"] == user and not grant["temporary"], grant
        machine.succeed(f"id -nG {user} | grep -w nostr")
        uid = machine.succeed(f"id -u {user}").strip()
        assert 30000 <= int(uid) <= 39999, uid
        machine.succeed(f"grep {pk} /home/{user}/.config/kwak/identity.json")
        machine.succeed(f"runuser -u {user} -- sh -c 'echo x >> ~/.config/kitty/kitty.conf'")
        machine.succeed(f"grep -x {pk} /home/{user}/welcome.txt")
        machine.succeed(f"passwd -S {user} | grep -w L")
        machine.succeed(f"grep ncryptsec1 /var/lib/kwak-userd/keys/{user}/key.json")
        machine.fail(f"grep -r {sk} /var/lib/kwak-userd")
        assert [p["username"] for p in call("list_known")] == [user]

    with subtest("tokens are single use and only for their user"):
        assert pam("n0123456789", grant["token"]) != 0
        grant = call("unlock", username=user, password="hunter2")
        assert pam(user, grant["token"], "open_session", "close_session") == 0
        assert pam(user, grant["token"]) != 0
        machine.fail(f"runuser -u {user} -- ${client} list_known")

    with subtest("a saved identity survives logout and signs out explicitly"):
        machine.succeed(f"getent passwd {user}")
        machine.succeed(f"runuser -u {user} -- touch /tmp/leftover")
        call("signout", as_user=user)
        assert_gone(user, uid)

    with subtest("nsec without a password is temporary and deleted at logout"):
        sk, pk, guest = new_key()
        grant = call("login_nsec", nsec=sk)
        assert grant["temporary"], grant
        guid = machine.succeed(f"id -u {guest}").strip()
        machine.fail(f"test -e /var/lib/kwak-userd/keys/{guest}")
        assert [p["temporary"] for p in call("list_known")] == [True]
        assert pam(guest, call("unlock", username=guest)["token"], "open_session") == 0
        machine.succeed(f"runuser -u {guest} -- touch /tmp/guest-file")
        machine.succeed(f"echo x | pamtester greetd {guest} close_session")
        assert_gone(guest, guid)

    with subtest("leftover temporary identities are removed at boot"):
        sk, pk, guest = new_key()
        call("login_nsec", nsec=sk)
        machine.succeed("systemctl restart kwak-userd-cleanup.service")
        machine.fail(f"getent passwd {guest}")

    with subtest("ncryptsec is saved and unlocked with its password"):
        sk, pk, user = new_key()
        ncryptsec = machine.succeed(f"echo {sk} | nak key encrypt secretpw").strip()
        grant = call("login_ncryptsec", ncryptsec=ncryptsec, password="secretpw")
        assert grant["username"] == user and not grant["temporary"], grant
        machine.succeed(f"grep {ncryptsec} /var/lib/kwak-userd/keys/{user}/key.json")
        machine.fail(f"runuser -u greeter -- ${client} unlock '{{\"username\": \"{user}\", \"password\": \"nope\"}}'")
        assert pam(user, call("unlock", username=user, password="secretpw")["token"]) == 0
        machine.succeed(f"kwak-userd remove {user}")

    with subtest("bunker sign-in confirms the pubkey and is saved"):
        remote = machine.succeed("nak key generate").strip()
        rpk = machine.succeed(f"echo {remote} | nak key public").strip()
        machine.succeed(
            f"systemd-run --unit=remote-bunker -E HOME=/root -E NOSTR_SECRET_KEY={remote} "
            "-p StandardOutput=null nak bunker -s s3cret ${relay}"
        )
        uri = f"bunker://{rpk}?relay=ws%3A%2F%2F127.0.0.1%3A10547&secret=s3cret"
        grant = json.loads(machine.wait_until_succeeds(
            f"runuser -u greeter -- ${client} login_bunker {shlex.quote(json.dumps({'uri': uri}))}",
            timeout=120,
        ))
        user = "n" + rpk[:10]
        assert grant["username"] == user and not grant["temporary"], grant
        assert call("list_known")[0]["method"] == "bunker"
        assert pam(user, call("unlock", username=user)["token"]) == 0

    with subtest("only root can remove an identity"):
        machine.fail(f"runuser -u greeter -- ${client} remove '{{\"username\": \"{user}\"}}'")
        machine.succeed(f"kwak-userd remove {user}")
        machine.fail(f"getent passwd {user}")
  '';
}
