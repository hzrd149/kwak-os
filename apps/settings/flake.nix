{
  description = "kwakOS Settings: choose the Hyprland window tiling mode";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      forAllSystems = nixpkgs.lib.genAttrs [
        "x86_64-linux"
        "aarch64-linux"
      ];
    in
    {
      packages = forAllSystems (system: {
        # kwakOS pins its own Hyprland; standalone builds use the desktop's hyprctl.
        default = nixpkgs.legacyPackages.${system}.callPackage ./package.nix { hyprland = null; };
      });

      # The package runs the unit tests in its checkPhase.
      checks = forAllSystems (system: {
        default = self.packages.${system}.default;
      });

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          package = self.packages.${system}.default;
        in
        {
          default = pkgs.mkShell {
            inputsFrom = [ package ];
            packages = [ package.python ];
          };
        }
      );

      formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.nixfmt-tree);
    };
}
