# Napplets through kwakore, a per-user service. Every Nostr identity gets it,
# signed in as that identity from login: kwakore's system signer asks
# kwak-userd, which holds the key in memory for the session.
{
  config,
  lib,
  kwakore,
  ...
}:
let
  package = config.programs.kwakore.package;
in
{
  imports = [ kwakore.nixosModules.default ];

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
  };
}
