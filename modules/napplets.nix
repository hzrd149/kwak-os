# Napplets through kwakore, a per-user service. Every Nostr identity gets it,
# signed in as that identity from login: kwakore's system signer asks
# kwak-userd, which holds the key in memory for the session.
{
  config,
  lib,
  pkgs,
  kwakore,
  ...
}:
let
  package = config.programs.kwakore.package;
  defaults = config.kwak.nostrUsers.defaultNapplets;
  manifest = pkgs.writeText "kwak-default-napplets.json" (builtins.toJSON defaults);
  installer = pkgs.writeShellScript "kwak-install-default-napplets" ''
    exec ${pkgs.python3}/bin/python3 ${../apps/userd/install_default_napplets.py} ${package}/bin/kwakore
  '';
in
{
  imports = [ kwakore.nixosModules.default ];

  options.kwak.nostrUsers.defaultNapplets = lib.mkOption {
    type = lib.types.listOf lib.types.str;
    default = [ ];
    example = [ "nostr:naddr1..." ];
    description = ''
      Napplet addresses copied into each new Nostr user's home at account
      creation and installed on their first sign-in. Failed installs retry on
      later sign-ins. Successfully installed defaults are never reinstalled
      after the user removes them. Changes apply to new accounts only.
    '';
  };

  config = lib.mkIf config.kwak.nostrUsers.enable {
    programs.kwakore = {
      enable = true;
      # Identity users are created at sign-in, so they are matched by group.
      groups = [ "nostr" ];
      settings.signer = {
        mode = "system";
        socket = "/run/kwak-userd.sock";
      };
    };
    # makeWrapper execs this; it is what /proc/PID/exe shows for the daemon.
    kwak.nostrUsers.signerClients = [ "${package}/bin/.kwakore-daemon-wrapped" ];
    kwak.nostrUsers.setupHooks = lib.optionalAttrs (defaults != [ ]) {
      "10-default-napplets" = ''
        ${pkgs.coreutils}/bin/install -m 600 -o "$KWAK_USER" -g "$KWAK_USER" \
          ${manifest} "$KWAK_HOME/.config/kwak/default-napplets.json"
      '';
    };
    systemd.user.services.kwak-default-napplets = {
      description = "Install default napplets for a new Nostr user";
      wantedBy = [ "default.target" ];
      wants = [ "kwakore.socket" ];
      after = [ "kwakore.socket" ];
      unitConfig = {
        ConditionGroup = "nostr";
        ConditionPathExists = "%h/.config/kwak/default-napplets.json";
      };
      serviceConfig = {
        Type = "oneshot";
        ExecStart = installer;
      };
    };
  };
}
