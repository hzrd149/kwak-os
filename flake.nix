{
  description = "kwakOS — NixOS with a Hyprland desktop";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";
    # Keep Hyprland's own dependency pins and matching portal together.
    hyprland.url = "github:hyprwm/Hyprland/v0.56.2";
    # Swipe card codecs and the MSR90 reader. Only its Python library is used:
    # its package adds the TUI's dependencies, and its NixOS module lets
    # sessions read the reader, which kwak-cards keeps to itself.
    nostr-swipe-cards = {
      url = "git+https://relay.ngit.dev/npub1ye5ptcxfyyxl5vjvdjar2ua3f0hynkjzpx552mu5snj3qmx5pzjscpknpr/nostr-swipe-cards.git";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    # The napplet runtime, a per-user service that signs through kwak-userd.
    kwakore = {
      url = "github:hzrd149/kwakore";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      hyprland,
      nostr-swipe-cards,
      kwakore,
    }:
    let
      system = "x86_64-linux";
      # Resolve every locked input while building the image. The installer uses
      # these exact paths/metadata without needing Nix's fetcher cache or internet.
      lockedInputs = (builtins.fromJSON (builtins.readFile ./flake.lock)).nodes;
      installerInputs = pkgs.writeText "kwakos-offline-inputs.json" (
        builtins.toJSON (
          nixpkgs.lib.mapAttrs (
            _: node:
            let
              info = builtins.fetchTree node.locked;
            in
            {
              path = info.outPath;
              metadata = builtins.removeAttrs info [
                "outPath"
                "__toString"
              ];
            }
          ) (nixpkgs.lib.filterAttrs (_: node: node ? locked) lockedInputs)
        )
      );
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
        # Each app under apps/ is also a standalone flake; the OS builds them
        # from their package.nix with this flake's pinned nixpkgs.
        kwak-settings = final.callPackage ./apps/settings/package.nix { };
        kwak-userd = final.callPackage ./apps/userd/package.nix { };
        kwak-greeter = final.callPackage ./apps/greeter/package.nix { };
        kwak-cards = final.callPackage ./apps/cards/package.nix { skcSource = nostr-swipe-cards; };
        calamares-nixos-extensions = final.callPackage ./packages/calamares-nixos-extensions.nix {
          calamares-nixos-extensions = prev.calamares-nixos-extensions;
          kwakSource = self.outPath;
          kwakInputs = installerInputs;
        };
        calamares-nixos =
          let
            core = prev.calamares.overrideAttrs (old: {
              postPatch = (old.postPatch or "") + ''
                substituteInPlace src/modules/finished/FinishedPage.cpp \
                  --replace-fail 'using the %2 Live environment.' \
                  'using the %2 Live environment.<br/><br/>Remove the USB stick as the computer restarts.<br/>To use your local account, choose Sign in with another account, then Linux user, and enter the username and password you chose during installation.'
              '';
            });
          in
          final.symlinkJoin {
            name = "kwakos-installer-${core.version}";
            paths = [ core ];
            inherit (core) meta;
            # Configuration/source changes should not recompile Calamares itself.
            postBuild = ''
              rm $out/bin/calamares $out/share/applications/calamares.desktop
              cp ${./config/calamares/calamares.desktop} $out/share/applications/calamares.desktop
              substituteInPlace $out/share/applications/calamares.desktop \
                --replace-fail '@INSTALLER@' "$out/bin/kwak-install"
              # Match the outer launcher so pkexec preserves DISPLAY/XAUTHORITY.
              policy=share/polkit-1/actions/io.calamares.calamares.policy
              rm "$out/$policy"
              cp "${core}/$policy" "$out/$policy"
              substituteInPlace "$out/$policy" \
                --replace-fail '${core}/bin/calamares' "$out/bin/calamares"
              cat > $out/bin/calamares <<EOF
              #!${final.runtimeShell}
              if [ ! -d /sys/firmware/efi ]; then
                ${final.kdePackages.kdialog}/bin/kdialog --error 'KwakOS requires UEFI. Restart and choose the UEFI entry for this USB stick in your boot menu. No disks have been changed.'
                exit 1
              fi
              export XDG_DATA_DIRS="${final.calamares-nixos-extensions}/share:\''${XDG_DATA_DIRS:-/run/current-system/sw/share}"
              export XDG_CONFIG_DIRS="${final.calamares-nixos-extensions}/etc:\''${XDG_CONFIG_DIRS:-/etc/xdg}"
              exec ${core}/bin/calamares --xdg-config "\$@"
              EOF
              chmod +x $out/bin/calamares
              # Hyprland's Xwayland authorizes its session user, not root. Grant
              # only the local installer process's user access before pkexec.
              cat > $out/bin/kwak-install <<EOF
              #!${final.runtimeShell}
              set -e
              ${final.xhost}/bin/xhost +SI:localuser:root >/dev/null
              exec /run/wrappers/bin/pkexec $out/bin/calamares "\$@"
              EOF
              chmod +x $out/bin/kwak-install
            '';
          };
      };
      pkgs = import nixpkgs {
        inherit system;
        overlays = [ desktopOverlay ];
      };
      mkHost =
        host: extraModules:
        nixpkgs.lib.nixosSystem {
          inherit system;
          specialArgs = { inherit hyprland kwakore; };
          modules = [
            { nixpkgs.overlays = [ desktopOverlay ]; }
            ./modules/base.nix
            ./modules/desktop.nix
            ./modules/users.nix
            ./modules/napplets.nix
            host
          ]
          ++ extraModules;
        };
    in
    {
      nixosConfigurations = {
        vm = mkHost ./hosts/vm [ ];
        physical = mkHost ./hosts/physical [ ];
        iso = mkHost ./hosts/iso [
          {
            # Prebuilt target packages and the tools used to assemble personalized
            # activation scripts, users, filesystems and initrd. Avoid bundling the
            # entire compiler/source bootstrap graph of every desktop application.
            isoImage.storeContents = [
              self.nixosConfigurations.physical.config.system.build.toplevel
              installerInputs
              pkgs.hyprland.src
              pkgs.stdenvNoCC
              pkgs.stdenv
              pkgs.bintools
              pkgs.makeWrapper
              pkgs.perl
              pkgs.python3
              pkgs.jq
              pkgs.rsync
              pkgs.kmod
              pkgs.cpio
              pkgs.zstd
              pkgs.xz
              pkgs.gzip
              pkgs.cryptsetup
              pkgs.btrfs-progs
              pkgs.xfsprogs
              pkgs.f2fs-tools
              pkgs.dosfstools
              pkgs.shellcheck-minimal
              pkgs.lndir
              pkgs.kbd.dev
              pkgs.kmod.dev
              pkgs.systemdMinimal.out
              pkgs.perlPackages.ConfigIniFiles
              pkgs.perlPackages.FileSlurp
              pkgs.perlPackages.JSON
              pkgs.perlPackages.ListCompare
              pkgs.perlPackages.XMLLibXML
              pkgs.libxml2.bin
              pkgs.libxslt.bin
              pkgs.docbook5
              pkgs.docbook_xsl_ns
              pkgs.texinfo
              (pkgs.python3.withPackages (p: [ p.mistune ]))
              pkgs.mypy
              self.nixosConfigurations.physical.config.boot.bootspec.package
              self.nixosConfigurations.physical.config.hardware.cpu.intel.microcodePackage
              self.nixosConfigurations.physical.config.hardware.cpu.amd.microcodePackage
            ];
          }
        ];
      };

      packages.${system} = {
        inherit (pkgs)
          hyprland
          hyprflow
          hyprlax
          wofi
          kwak-settings
          kwak-userd
          kwak-greeter
          kwak-cards
          kwak-hyprland-config
          ;
        vm = self.nixosConfigurations.vm.config.system.build.vm;
        iso = self.nixosConfigurations.iso.config.system.build.isoImage;
        installer-inputs = installerInputs;
        installer = pkgs.calamares-nixos-extensions;
        installer-store = pkgs.closureInfo {
          rootPaths = self.nixosConfigurations.iso.config.isoImage.storeContents;
        };
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
        iso-usb-boot = import ./tests/iso-usb-boot.nix {
          inherit pkgs;
          iso = self.packages.${system}.iso;
        };
        installer = pkgs.runCommand "kwak-installer-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
          python3 ${self.outPath}/tests/installer.py
          export PYTHONPYCACHEPREFIX="$TMPDIR/pycache"
          python3 -m py_compile ${pkgs.calamares-nixos-extensions}/lib/calamares/modules/nixos/main.py
          python3 - <<'PYTHON'
          import configparser
          import xml.etree.ElementTree as ET
          package = "${pkgs.calamares-nixos}"
          policy = ET.parse(package + "/share/polkit-1/actions/io.calamares.calamares.policy")
          annotations = {item.attrib["key"]: item.text for item in policy.findall(".//annotate")}
          assert annotations["org.freedesktop.policykit.exec.path"] == package + "/bin/calamares"
          assert annotations["org.freedesktop.policykit.exec.allow_gui"] == "true"
          desktop = configparser.ConfigParser(interpolation=None)
          desktop.read(package + "/share/applications/calamares.desktop")
          assert desktop["Desktop Entry"]["Exec"] == package + "/bin/kwak-install"
          PYTHON
          touch $out
        '';
        installer-offline = import ./tests/installer-offline.nix {
          inherit pkgs installerInputs;
          source = self.outPath;
          storePaths = self.nixosConfigurations.iso.config.isoImage.storeContents;
          expectedPortal = builtins.unsafeDiscardStringContext self.nixosConfigurations.physical.config.programs.hyprland.portalPackage.drvPath;
          expectedHyprland = builtins.unsafeDiscardStringContext pkgs.hyprland.drvPath;
        };
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
          assert !live.kwak.nostrUsers.enable;
          assert self.nixosConfigurations.physical.config.services.greetd.enable;
          pkgs.runCommand "kwak-iso-config-check" { } "touch $out";
        # App unit tests run in each package's checkPhase.
        inherit (pkgs) kwak-settings kwak-userd kwak-greeter;
        window-modes = pkgs.runCommand "kwak-window-modes-check" { nativeBuildInputs = [ pkgs.lua5_5 ]; } ''
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
        userd = import ./tests/userd-vm.nix {
          inherit pkgs;
          usersModule = ./modules/users.nix;
        };
        vm = self.nixosConfigurations.vm.config.system.build.toplevel;
        physical = self.nixosConfigurations.physical.config.system.build.toplevel;
      };
    };
}
