{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.kwak.nostrUsers;
  userd = lib.getExe pkgs.kwak-userd;
  cards = lib.getExe pkgs.kwak-cards;
  coreutils = "${pkgs.coreutils}/bin";
  greeter = "${pkgs.cage}/bin/cage -s -m last -- ${lib.getExe pkgs.kwak-greeter}";
  hyprlockPam = config.security.pam.services.hyprlock.rules;
  # A second greetd on the next free VT, started by kwak-userd when a card is
  # swiped during a session. Its greeter handles that swipe, then closes it.
  switchGreeter = pkgs.writeShellScript "kwak-greeter-switch" ''
    set -eu
    case "$1" in "" | *[!0-9a-f]*) exit 2 ;; esac
    cat > "$RUNTIME_DIRECTORY/greetd.toml" <<EOF
    [terminal]
    vt = "next"
    switch = true

    [default_session]
    user = "greeter"
    command = "${greeter} --switch $1"
    EOF
    exec ${lib.getExe config.services.greetd.package} --config "$RUNTIME_DIRECTORY/greetd.toml"
  '';
  hyprlockConfig = pkgs.writeText "hyprlock.conf" ''
    general {
      hide_cursor = false
      # Bunker accounts unlock by approving on their signer: Enter with no password.
      ignore_empty_input = false
    }
    background {
      monitor =
      color = rgb(000000)
    }
    label {
      monitor =
      text = LOCKED · $DESC ($USER)
      color = rgb(33ff66)
      font_size = 18
      font_family = DejaVu Sans Mono
      position = 0, 60
      halign = center
      valign = center
    }
    input-field {
      monitor =
      size = 520, 44
      outline_thickness = 2
      rounding = 0
      outer_color = rgb(33ff66)
      inner_color = rgb(030803)
      font_color = rgb(c8ffd4)
      check_color = rgb(ffcc33)
      fail_color = rgb(ff5f5f)
      font_family = DejaVu Sans Mono
      fade_on_empty = false
      placeholder_text = Password · Enter for your signer · or swipe your card
      fail_text = $FAIL
      position = 0, 0
      halign = center
      valign = center
    }
  '';
  hypridleConfig = pkgs.writeText "hypridle.conf" ''
    general {
      lock_cmd = pidof hyprlock || hyprlock --config ${hyprlockConfig}
      # kwak-userd unlocks a session after a swipe of its account's card.
      unlock_cmd = pkill -USR1 hyprlock
      before_sleep_cmd = loginctl lock-session
    }
  '';
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
    cards.enable = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Sign in by swiping a Nostr swipe card (SKC1 or SKC2) on an MSR90 reader.
        The reader service starts when the reader is plugged in.
      '';
    };
    groups = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "nostr" ] ++ lib.optional config.networking.networkmanager.enable "networkmanager";
      defaultText = lib.literalExpression ''[ "nostr" ] ++ lib.optional config.networking.networkmanager.enable "networkmanager"'';
      description = "Supplementary groups of every Nostr identity user.";
    };

    signerClients = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        Executables, as /proc/PID/exe shows them, that may ask kwak-userd to
        sign as the user they run as. They must ask the user first.
      '';
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
      "kwak/userd.json".text = builtins.toJSON {
        inherit (cfg) relays groups;
        card_user = "kwak-cards";
        signer_clients = cfg.signerClients;
      };
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
    systemd.services.kwak-userd-refresh = {
      description = "Refresh Nostr identity profiles";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${userd} refresh-profiles";
      };
    };
    systemd.timers.kwak-userd-refresh = {
      description = "Periodically refresh Nostr identity profiles";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "5min";
        OnUnitActiveSec = "6h";
        Persistent = true;
      };
    };

    # Swipe cards: udev starts a reader service for each MSR90 that is plugged
    # in. Only that service can read the reader (no uaccess for sessions), and it
    # keeps the reader's keyboard output inhibited, so swipes are never typed
    # into a window. kwak-userd ignores swipes unless the greeter is waiting.
    users.users.kwak-cards = lib.mkIf cfg.cards.enable {
      isSystemUser = true;
      group = "kwak-cards";
    };
    users.groups.kwak-cards = lib.mkIf cfg.cards.enable { };
    services.udev.extraRules = lib.mkIf cfg.cards.enable ''
      SUBSYSTEM=="hidraw", ATTRS{idVendor}=="c216", ATTRS{idProduct}=="0180", GROUP="kwak-cards", MODE="0660", TAG+="systemd", ENV{SYSTEMD_WANTS}+="kwak-card-reader@%k.service"
      ACTION=="add", SUBSYSTEM=="input", KERNEL=="input[0-9]*", ATTR{name}=="HID c216:0180", ATTRS{idVendor}=="c216", ATTRS{idProduct}=="0180", RUN+="${coreutils}/chgrp kwak-cards /sys%p/inhibited", RUN+="${coreutils}/chmod 0664 /sys%p/inhibited"
    '';
    systemd.services."kwak-card-reader@" = lib.mkIf cfg.cards.enable {
      description = "Nostr swipe card reader on %I";
      bindsTo = [ "dev-%i.device" ];
      after = [
        "dev-%i.device"
        "kwak-userd.socket"
      ];
      serviceConfig = {
        ExecStart = "${cards} read /dev/%I";
        User = "kwak-cards";
        Group = "kwak-cards";
        Restart = "on-failure";
        RestartSec = 2;
        NoNewPrivileges = true;
        PrivateNetwork = true;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        DevicePolicy = "closed";
        DeviceAllow = [ "/dev/%I r" ];
        RestrictAddressFamilies = [ "AF_UNIX" ];
        # Writing the reader's /sys/.../inhibited needs a writable /sys.
        ProtectKernelTunables = false;
      };
    };

    services.greetd = {
      enable = true;
      settings.default_session.command = greeter;
    };
    systemd.services.greetd = {
      # Type=idle holds the greeter back up to 5s until other boot jobs are
      # dispatched; that only matters for text greeters, and this one runs in cage.
      serviceConfig.Type = lib.mkForce "simple";
      # Start kwak-userd alongside the greeter, so it is up by the first request.
      wants = [ "kwak-userd.service" ];
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

    systemd.services."kwak-greeter-switch@" = {
      description = "Account switching sign-in screen %i";
      wants = [ "systemd-user-sessions.service" ];
      after = [ "systemd-user-sessions.service" ];
      serviceConfig = {
        ExecStart = "${switchGreeter} %i";
        RuntimeDirectory = "kwak-greeter-switch/%i";
        # As for greetd.service.
        IgnoreSIGPIPE = false;
        SendSIGHUP = true;
        TimeoutStopSec = "30s";
        KeyringMode = "shared";
      };
      # Don't end a session that was started from it.
      restartIfChanged = false;
    };

    # Switching accounts locks the session left behind. hypridle runs hyprlock
    # on loginctl lock-session; Nostr identities unlock with their password,
    # their signer, or a swipe of their card, and other users with pam_unix.
    programs.hyprlock.enable = true;
    systemd.user.services.hypridle.serviceConfig.ExecStart = [
      ""
      "${lib.getExe config.services.hypridle.package} --config ${hypridleConfig}"
    ];
    security.pam.services.hyprlock.rules.auth.kwak-userd = {
      order = hyprlockPam.auth.unix.order - 10;
      control = "sufficient";
      modulePath = pamExec;
      args = [
        "expose_authtok"
        "quiet"
        userd
        "pam-unlock"
      ];
    };

    environment.systemPackages = [
      pkgs.kwak-userd
      pkgs.kwak-greeter
      pkgs.nak
    ];
  };
}
