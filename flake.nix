{
  description = "kwakOS — stock NixOS with a Hyprland desktop";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs =
    { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      mkHost =
        host:
        nixpkgs.lib.nixosSystem {
          inherit system;
          modules = [
            ./modules/base.nix
            ./modules/desktop.nix
            host
          ];
        };
    in
    {
      nixosConfigurations = {
        vm = mkHost ./hosts/vm;
        physical = mkHost ./hosts/physical;
      };

      packages.${system} = {
        vm = self.nixosConfigurations.vm.config.system.build.vm;
        default = self.packages.${system}.vm;
      };

      apps.${system} = {
        vm = {
          type = "app";
          meta.description = "Run the local kwakOS QEMU VM";
          program = "${self.packages.${system}.vm}/bin/run-kwakos-vm-vm";
        };
        default = self.apps.${system}.vm;
      };

      formatter.${system} = pkgs.nixfmt-tree;
      checks.${system} = {
        vm = self.nixosConfigurations.vm.config.system.build.toplevel;
        physical = self.nixosConfigurations.physical.config.system.build.toplevel;
      };
    };
}
