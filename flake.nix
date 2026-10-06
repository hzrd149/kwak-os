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
      desktopOverlay = final: prev: {
        hyprland = desktopHyprland;
        hyprlax = final.callPackage ./packages/hyprlax.nix { inherit (prev) hyprlax; };
        wofi = final.callPackage ./packages/wofi.nix { inherit (prev) wofi; };
        kwak-settings = final.callPackage ./packages/settings.nix { };
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
      };

      packages.${system} = {
        inherit (pkgs)
          hyprland
          hyprlax
          wofi
          kwak-settings
          ;
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
              --verify-config --config ${./config/hypr/hyprland.lua} > config-check.log 2>&1; then
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
            global_config = config["global"]
            assert global_config["parallax"] == {"input": "workspace", "shift_percent": 5}
            assert global_config["render"] == {
                "tile": {"x": True, "y": False}, "content_scale": 1.0
            }
            assert (global_config["fps"], global_config["duration"],
                    global_config["vsync"], global_config["easing"]) == (144, 4.0, False, "expo")
            layers = global_config["layers"]
            assert [layer["path"] for layer in layers] == [
                "./4.png", "./3.png", "./2.png", "./1.png", "./0.png"
            ], "Expected exactly the five supplied images, back to front"
            speeds = {layer["path"]: layer["shift_multiplier"] for layer in layers}
            assert speeds["./0.png"] == speeds["./4.png"] == 0.0
            assert speeds["./1.png"] > speeds["./2.png"] > speeds["./3.png"] > 0.0
            assert sorted(p.name for p in demo.glob("*.png")) == [
                "0.png", "1.png", "2.png", "3.png", "4.png"
            ], "No stock demo images should be installed"
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
