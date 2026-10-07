# Evaluate the locked flake graph from bundled sources, without a fetcher cache
# or network. Preserve source metadata (notably Hyprland's revision/version),
# which --override-input ... path:... would otherwise change.
{ source, manifest }:
let
  lock = builtins.fromJSON (builtins.readFile (source + "/flake.lock"));
  sources = builtins.fromJSON (builtins.unsafeDiscardStringContext (builtins.readFile manifest));
  resolve = input: if builtins.isList input then follow lock.root input else input;
  follow =
    node: path:
    if path == [ ] then
      node
    else
      follow (resolve lock.nodes.${node}.inputs.${builtins.head path}) (builtins.tail path);
  nodes = builtins.mapAttrs (
    name: node:
    let
      info =
        if name == lock.root then
          { outPath = source; }
        else
          # Reattach Nix's store dependency context after decoding JSON. Without
          # it, packages using src = self get different derivation identities
          # and Nix tries to rebuild the prebuilt desktop dependencies.
          sources.${name}.metadata // { outPath = builtins.storePath sources.${name}.path; };
      inputs = builtins.mapAttrs (_: input: nodes.${resolve input}) (node.inputs or { });
      flake = import (info.outPath + "/flake.nix");
      result =
        if node.flake or true then
          (flake.outputs (inputs // { self = result; }))
          // info
          // {
            inherit inputs;
            sourceInfo = info;
            _type = "flake";
          }
        else
          info;
    in
    if name != lock.root && (info.narHash or null) != node.locked.narHash then
      throw "Bundled input ${name} no longer matches flake.lock. After updating, rebuild with --flake /etc/nixos/kwak-os#physical."
    else
      result
  ) lock.nodes;
in
nodes.${lock.root}
