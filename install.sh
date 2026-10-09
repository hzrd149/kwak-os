#!/usr/bin/env bash
# Convert an installed NixOS system to kwakOS, or update an existing deployment.
set -euo pipefail

die() { echo "kwakOS: $*" >&2; exit 1; }
usage() {
  cat <<'EOF'
Usage: sudo bash install.sh [--yes] [--switch] [--admin-user USER]
                            [--flake /absolute/path#HOST]

Without --flake, manage /etc/nixos#kwakos, converting a conventional NixOS
configuration or an ISO-installed kwakOS system on the first run.
Custom flakes must already import the kwakOS module through an input named
kwakOS. Only that input is updated; your other dependency pins are preserved.

--yes              Skip confirmation (otherwise read from /dev/tty).
--switch           Activate now instead of preparing the next boot.
--admin-user USER  Existing local administrator (defaults to the sudo caller).
--flake PATH#HOST  Update an existing custom flake, without rewriting it.
--help             Show this help.
EOF
}

yes=false
action=boot
admin=${SUDO_USER:-}
target=
while (($#)); do
  case "$1" in
    --yes) yes=true ;;
    --switch) action=switch ;;
    --admin-user|--flake)
      (($# >= 2)) || die "$1 needs a value"
      if [[ $1 == --admin-user ]]; then admin=$2; else target=$2; fi
      shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

[[ $EUID == 0 ]] || die "Run this script with sudo bash."
[[ -e /etc/NIXOS ]] || die "This script requires an installed NixOS or kwakOS system."
[[ $(uname -m) == x86_64 ]] || die "kwakOS currently supports x86_64 only."
for command in nix nixos-rebuild cp mktemp getent; do
  command -v "$command" >/dev/null || die "Required command not found: $command"
done
nix_cmd=(nix --extra-experimental-features 'nix-command flakes')
export NIX_CONFIG="${NIX_CONFIG:-}
experimental-features = nix-command flakes"

managed=false
legacy=false
if [[ -z $target ]]; then
  directory=/etc/nixos
  target=$directory#kwakos
  managed=true
  if [[ -e $directory/flake.nix ]]; then
    grep -q '^# Managed by kwakOS install.sh$' "$directory/flake.nix" ||
      die "Existing /etc/nixos/flake.nix is not managed by this script. Import kwakOS and use --flake /etc/nixos#HOST (see README)."
  elif [[ -f $directory/kwak-os/hosts/physical/installer-settings.nix && -f $directory/kwak-os/flake.nix ]]; then
    legacy=true
  else
    [[ -f $directory/configuration.nix ]] || die "Missing /etc/nixos/configuration.nix."
    [[ $admin =~ ^[a-z_][a-z0-9_-]*$ && $admin != root ]] ||
      die "Specify an existing local account with --admin-user USER."
    account=$(getent passwd "$admin") || die "Local account not found: $admin"
    IFS=: read -r _ _ uid _ _ _ _ <<< "$account"
    ((uid >= 1000 && uid < 65534)) || die "Choose a normal local account, not a system account."
  fi
else
  [[ $target == /*#* && $target != *$'\n'* ]] || die "--flake must be /absolute/path#HOST."
  directory=${target%#*}
  [[ -n ${target##*#} && -f $directory/flake.nix ]] || die "Invalid flake target: $target"
fi
# Force local path semantics: untracked configuration files in Git are included.
flake="path:$directory"
host=${target##*#}

echo "kwakOS will back up $directory, update kwakOS, and run nixos-rebuild $action."
echo "Conversion replaces the login screen with greetd and audio with PipeWire."
echo "Hardware, bootloader, accounts, and the original state version are retained."
if ! $yes; then
  read -r -p 'Continue? [y/N] ' reply </dev/tty || die "No terminal; use --yes to confirm."
  [[ $reply == y || $reply == Y ]] || die "Cancelled."
fi

mkdir -p /var/backups
backup=$(mktemp -d /var/backups/kwakos.XXXXXXXX)
cp -a "$directory" "$backup/nixos"
echo "Configuration backup: $backup/nixos"
trap 'echo "kwakOS failed. Configuration backup: $backup/nixos. No automatic restore was attempted; inspect the error before retrying." >&2' ERR

if $managed && [[ ! -e $directory/flake.nix ]]; then
  [[ ! -e $directory/kwakos-local.nix ]] || die "kwakos-local.nix already exists; review it before converting."
  if $legacy; then
    # The ISO copy stays intact; reuse its machine-local modules, not its old OS.
    state_version=$("${nix_cmd[@]}" eval --raw "path:$directory/kwak-os#nixosConfigurations.physical.config.system.stateVersion")
    [[ $state_version =~ ^[0-9]{2}\.[0-9]{2}$ ]] || die "Invalid original state version."
    machine_module="./kwak-os/hosts/physical { system.stateVersion = \"$state_version\"; }"
    admin_setting=
  else
    machine_module=./configuration.nix
    admin_setting="kwak.adminUser = \"$admin\";"
  fi
  cat > "$directory/flake.nix" <<EOF
# Managed by kwakOS install.sh
{
  inputs.kwakOS.url = "github:hzrd149/kwak-os";
  outputs = { kwakOS, ... }: {
    nixosConfigurations.kwakos = kwakOS.inputs.nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        kwakOS.nixosModules.default
        $machine_module
        ./kwakos-local.nix
      ];
    };
  };
}
EOF
  cat > "$directory/kwakos-local.nix" <<EOF
# Machine-local overrides. Keep configuration.nix and hardware settings intact.
{ lib, ... }: {
  $admin_setting
  services.displayManager.sddm.enable = lib.mkForce false;
  services.displayManager.gdm.enable = lib.mkForce false;
  services.xserver.displayManager.lightdm.enable = lib.mkForce false;
  services.xserver.displayManager.startx.enable = lib.mkForce false;
  services.displayManager.autoLogin.enable = lib.mkForce false;
  services.pulseaudio.enable = lib.mkForce false;
  services.pipewire.enable = lib.mkForce true;
  services.pipewire.pulse.enable = lib.mkForce true;
}
EOF
fi

# Check input names through Nix, not text matching or editing custom Nix code.
KWAKOS_FLAKE="$flake" "${nix_cmd[@]}" eval --raw --impure --expr \
  'let f = builtins.getFlake (builtins.getEnv "KWAKOS_FLAKE"); in f.inputs.kwakOS.outPath' >/dev/null
# Remote git inputs are fetched with the git binary, which stock NixOS lacks
# until kwakOS installs it; borrow it from kwakOS's pinned nixpkgs.
if ! command -v git >/dev/null; then
  git_path=$(KWAKOS_FLAKE="$flake" "${nix_cmd[@]}" build --no-link --print-out-paths --impure --expr \
    'let f = builtins.getFlake (builtins.getEnv "KWAKOS_FLAKE"); in f.inputs.kwakOS.inputs.nixpkgs.legacyPackages.x86_64-linux.git')
  export PATH="$git_path/bin:$PATH"
fi
"${nix_cmd[@]}" flake update kwakOS --flake "$flake"
# Source builds can outlast idle timeouts; suspending mid-build breaks downloads.
inhibit=()
if command -v systemd-inhibit >/dev/null; then
  inhibit=(systemd-inhibit --what=sleep:idle --who=kwakOS --why="Installing kwakOS")
fi
"${inhibit[@]}" nixos-rebuild "$action" --flake "$flake#$host" --no-update-lock-file
trap - ERR
if [[ $action == boot ]]; then
  echo "kwakOS is ready. Reboot when convenient to start the new system."
else
  echo "kwakOS activated. A reboot is recommended for a complete desktop update."
fi
echo "Run this script again with the same options to update."
