{
  description = "kwakOS — NixOS with a Hyprland desktop";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    # Keep Hyprland's own dependency pins and matching portal together.
    hyprland.url = "github:hyprwm/Hyprland/v0.56.2";
  };

  outputs =
    {
      self,
      nixpkgs,
      hyprland,
    }:
    let
      system = "x86_64-linux";
      desktopHyprland =
        hyprland.inputs.nixpkgs.legacyPackages.${system}.callPackage ./packages/hyprland.nix
          {
            inherit (hyprland.packages.${system}) hyprland;
          };
      desktopHyprflow =
        hyprland.inputs.nixpkgs.legacyPackages.${system}.callPackage ./packages/hyprflow.nix
          {
            hyprland = desktopHyprland;
          };
      desktopOverlay = final: prev: {
        hyprland = desktopHyprland;
        hyprflow = desktopHyprflow;
        kwak-hyprland-config = final.writeText "hyprland.lua" (
          builtins.readFile ./config/hypr/hyprland.lua
          + "\n"
          + builtins.replaceStrings [ "@HYPRFLOW_PLUGIN@" ] [ "${final.hyprflow}/lib/hyprflow.so" ] (
            builtins.readFile ./config/hypr/hyprflow.lua
          )
        );
        hyprlax = final.callPackage ./packages/hyprlax.nix { inherit (prev) hyprlax; };
        wofi = final.callPackage ./packages/wofi.nix { inherit (prev) wofi; };
        kwak-settings = final.callPackage ./packages/settings.nix { };
        calamares-nixos-extensions = final.callPackage ./packages/calamares-nixos-extensions.nix {
          calamares-nixos-extensions = prev.calamares-nixos-extensions;
          kwakSource = self.outPath;
        };
        calamares-nixos = prev.calamares.override {
          extraWrapperArgs = [
            "--prefix XDG_DATA_DIRS : ${final.calamares-nixos-extensions}/share"
            "--prefix XDG_CONFIG_DIRS : ${final.calamares-nixos-extensions}/etc"
            "--add-flag --xdg-config"
          ];
        };
      };
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ desktopOverlay ];
      };
      mkHost =
        host:
        nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs = { inherit hyprland; };
          modules = [
            { nixpkgs.overlays = [ desktopOverlay ]; }
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
        iso = mkHost ./hosts/iso;
      };

      packages.${system} = {
        inherit (pkgs)
          hyprland
          hyprflow
          hyprlax
          wofi
          kwak-settings
          kwak-hyprland-config
          ;
        vm = self.nixosConfigurations.vm.config.system.build.vm;
        iso = self.nixosConfigurations.iso.config.system.build.isoImage;
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
        iso-config =
          let
            live = self.nixosConfigurations.iso.config;
          in
          assert live.isoImage.makeEfiBootable && live.isoImage.makeBiosBootable;
          assert live.isoImage.makeUsbBootable;
          assert live.services.displayManager.autoLogin.user == "kwak";
          assert live.users.users.kwak.initialHashedPassword == "";
          assert !live.services.openssh.enable;
          assert !live.security.sudo.wheelNeedsPassword;
          assert live.fileSystems."/".fsType == "tmpfs";
          assert self.nixosConfigurations.physical.config.users.users.kwak.initialHashedPassword == null;
          pkgs.runCommand "kwak-iso-config-check" { } "touch $out";
        settings =
          pkgs.runCommand "kwak-settings-check"
            {
              nativeBuildInputs = [
                pkgs.python3
                pkgs.lua5_5
              ];
            }
            ''
              cp -r ${./apps} apps
              mkdir tests
              cp ${./tests/test_settings.py} tests/test_settings.py
              python3 -m unittest discover -s tests -p test_settings.py
              lua ${./tests/window-modes.lua} ${./config/hypr/hyprland.lua}
              touch $out
            '';
        desktop-config =
          assert nixpkgs.lib.hasPrefix "0.56.2+" pkgs.hyprland.version;
          pkgs.runCommand "kwak-desktop-config-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
            export XDG_RUNTIME_DIR="$TMPDIR/runtime"
            mkdir -m 700 "$XDG_RUNTIME_DIR"
            if ! ${pkgs.hyprland}/bin/Hyprland \
              --verify-config --config ${pkgs.kwak-hyprland-config} > config-check.log 2>&1; then
              cat config-check.log
              exit 1
            fi
            cat config-check.log
            grep -x 'config ok' config-check.log
            python3 - <<'PYTHON'
            import pathlib
            import tomllib

            demo = pathlib.Path("${pkgs.hyprlax}/share/hyprlax/pixel-city")
            config = tomllib.loads((demo / "parallax.toml").read_text())
            layers = config["global"]["layers"]
            assert len(layers) == 6, "Expected the stock six-layer pixel-city demo"
            for layer in layers:
                image = demo / layer["path"]
                assert image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), image
            PYTHON
            touch $out
          '';
        vm = self.nixosConfigurations.vm.config.system.build.toplevel;
        physical = self.nixosConfigurations.physical.config.system.build.toplevel;
      };
    };
}
