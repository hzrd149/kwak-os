{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.kwak.nostrUsers;
  userd = lib.getExe pkgs.kwak-userd;
  pamExec = "${config.security.pam.package}/lib/security/pam_exec.so";
  greetdPam = config.security.pam.services.greetd.rules;
  homeFiles = pkgs.linkFarm "kwak-home-files" (
    lib.mapAttrsToList (name: path: { inherit name path; }) cfg.homeFiles
  );
  # Store files are read-only, so starter files are copied in as the user's own.
  homeFilesHook = pkgs.writeShellScript "kwak-home-files" ''
    set -eu
    cd ${homeFiles}
    ${pkgs.findutils}/bin/find -L . -type f -print0 | while IFS= read -r -d "" file; do
      ${pkgs.coreutils}/bin/install -D -m 644 -o "$KWAK_USER" -g "$KWAK_USER" \
        "$file" "$KWAK_HOME/$file"
      dir=$(${pkgs.coreutils}/bin/dirname "$file")
      while [ "$dir" != . ]; do
        ${pkgs.coreutils}/bin/chown "$KWAK_USER:$KWAK_USER" "$KWAK_HOME/$dir"
        dir=$(${pkgs.coreutils}/bin/dirname "$dir")
      done
    done
  '';
in
{
  options.kwak.nostrUsers = {
    enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Sign in with Nostr identities through greetd, creating Unix users on demand.";
    };
    relays = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "wss://purplepag.es"
        "wss://relay.damus.io"
        "wss://nos.lol"
      ];
      description = "Relays used to look up profiles (kind 0) and relay lists (kind 10002).";
    };
    homeFiles = lib.mkOption {
      type = lib.types.attrsOf lib.types.path;
      default = { };
      example = lib.literalExpression ''{ ".config/kitty/kitty.conf" = ./kitty.conf; }'';
      description = ''
        Starter files copied into each new Nostr user's home folder, keyed by path
        relative to the home folder. The copies belong to the user, who can edit
        them; later rebuilds do not change existing homes.
      '';
    };
    setupHooks = lib.mkOption {
      type = lib.types.attrsOf lib.types.lines;
      default = { };
      example = lib.literalExpression ''{ "50-welcome" = "echo hi > \"$KWAK_HOME/welcome.txt\""; }'';
      description = ''
        Shell scripts run as root, in name order, after a Nostr user is created.
        They get KWAK_USER, KWAK_PUBKEY, and KWAK_HOME in their environment.
      '';
    };
    groups = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "nostr" ] ++ lib.optional config.networking.networkmanager.enable "networkmanager";
      defaultText = lib.literalExpression ''[ "nostr" ] ++ lib.optional config.networking.networkmanager.enable "networkmanager"'';
      description = "Supplementary groups of every Nostr identity user.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = config.users.mutableUsers;
        message = "kwak.nostrUsers creates users imperatively and needs users.mutableUsers.";
      }
    ];

    users.groups.nostr = { };

    environment.etc = {
      "kwak/userd.json".text = builtins.toJSON { inherit (cfg) relays groups; };
    }
    // lib.optionalAttrs (cfg.homeFiles != { }) {
      "kwak/user-setup.d/00-home-files".source = homeFilesHook;
    }
    // lib.mapAttrs' (
      name: text:
      lib.nameValuePair "kwak/user-setup.d/${name}" {
        source = pkgs.writeShellScript "kwak-setup-${name}" text;
      }
    ) cfg.setupHooks;
    systemd.tmpfiles.rules = [
      "d /var/lib/kwak-userd 0711 root root -"
      "d /etc/kwak/skel 0755 root root -"
      "d /etc/kwak/user-setup.d 0755 root root -"
    ];

    systemd.sockets.kwak-userd = {
      description = "Nostr identity user manager socket";
      wantedBy = [ "sockets.target" ];
      listenStreams = [ "/run/kwak-userd.sock" ];
      # Every request is authorized by the caller's SO_PEERCRED UID.
      socketConfig.SocketMode = "0666";
    };
    systemd.services.kwak-userd = {
      description = "Nostr identity user manager";
      requires = [ "kwak-userd.socket" ];
      after = [ "kwak-userd.socket" ];
      serviceConfig = {
        ExecStart = "${userd} serve";
        Restart = "on-failure";
      };
    };
    # Temporary identities are deleted at logout; this catches any left by a
    # crash or power loss before anyone can sign in again.
    systemd.services.kwak-userd-cleanup = {
      description = "Remove leftover temporary Nostr identities";
      wantedBy = [ "multi-user.target" ];
      before = [ "greetd.service" ];
      after = [ "local-fs.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${userd} cleanup";
      };
    };

    services.greetd = {
      enable = true;
      settings.default_session.command = "${pkgs.cage}/bin/cage -s -m last -- ${lib.getExe pkgs.kwak-greeter}";
    };
    # A one-time token from kwak-userd authenticates Nostr identities; local
    # accounts fall through to pam_unix with the same password prompt. Closing
    # the session of a temporary identity deletes it.
    security.pam.services.greetd.rules = {
      auth.kwak-userd = {
        order = greetdPam.auth.unix.order - 10;
        control = "sufficient";
        modulePath = pamExec;
        args = [
          "expose_authtok"
          "quiet"
          userd
          "pam-redeem"
        ];
      };
      session.kwak-userd = {
        order = greetdPam.session.unix.order + 10;
        control = "optional";
        modulePath = pamExec;
        args = [
          "type=close_session"
          "quiet"
          userd
          "pam-close"
        ];
      };
    };

    environment.systemPackages = [
      pkgs.kwak-userd
      pkgs.kwak-greeter
      pkgs.nak
    ];
  };
}
