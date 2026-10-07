# Live ISO

## Implementation status

The installer now requires internet access and uses standard flake installation.
Earlier images built successfully and booted into the desktop from simulated
USB storage, but the revised online image and a complete install/reboot/login
have not yet been verified. The latest Xwayland/root launcher fix and the USB
wizard-autostart regression test also remain unverified. Treat the installation
walkthrough below as the intended flow until these end-to-end checks pass.

kwakOS uses the ISO builder already included in its locked Nixpkgs input. The
`iso` host imports NixOS's `installation-cd-graphical-calamares.nix` alongside the same base and
desktop modules as the VM and physical hosts. No new flake input is required.
NixOS documents this approach in [Building a NixOS (Live) ISO](https://nixos.org/manual/nixos/stable/#sec-building-cd).

## Build

On x86_64 Linux with Nix and flakes enabled:

```sh
nix build .#iso --out-link result-iso
nix build --no-link .#checks.x86_64-linux.desktop-config .#checks.x86_64-linux.iso-config .#checks.x86_64-linux.installer
nix build --no-link .#checks.x86_64-linux.iso-usb-boot
nix flake check --no-build
sha256sum result-iso/iso/*.iso
```

Build before running a standalone `flake check --no-build` on a fresh store:
the pinned Hyprland derivation reads its filtered source during evaluation,
which needs materializing first with Nix 2.35.2. `flake.lock` is unchanged by
these commands. CI rejects lock-file updates.

The output is `result-iso/iso/kwakos-26.05-x86_64-linux.iso`. The image contains
the pinned desktop, installer tools, and generic hardware support. Its root is
in RAM; desktop changes and passwords set in the live session disappear on
reboot. It does not partition disks or install automatically.

SDDM logs into the desktop as `kwak`. NixOS's installer profile also provides
the `nixos` console account and `root`. These live accounts have empty passwords
and wheel users have passwordless sudo; SSH is disabled. These settings belong
only to `hosts/iso` and do not change the physical host's password policy.

Boot as a virtual CD/DVD or write the ISO to a USB drive using an image writer.
Writing an image replaces the selected drive's contents. The media boots in BIOS
and UEFI modes, but **installation is UEFI-only**. The installer refuses legacy-mode
installation before changing disks and explains how to restart using the USB's
UEFI entry. Secure Boot is not supported. Physical GPU compatibility still needs
testing on the intended machine.

Calamares opens automatically in the live desktop. It is also available as
**Install KwakOS** in the launcher (Super+Space). It walks through location,
keyboard, account, disk selection, and a final summary/confirmation. The guided
erase-disk path creates the partitions automatically. Internet access is required
and checked by the welcome page. Wired connections normally connect automatically;
for Wi-Fi, open **Connect to Network** from the launcher and choose **Activate a
connection**. There is no need to clone the repository.
See the [installation walkthrough](../README.md#install-on-a-physical-machine).

## Installation design

The ISO contains the live desktop, standard NixOS installation tools, and the
release's KwakOS source configuration and lock file. It does not bundle a separate
physical-system closure or maintain a custom offline flake evaluator.

The Calamares NixOS job copies the project to `/etc/nixos/kwak-os`, replaces the
hardware template with detected filesystems/kernel modules, and writes
`hosts/physical/installer-settings.nix`. The settings preserve the chosen local
administrator, hostname, locale, timezone, console/Hyprland keyboard, and
encrypted-swap devices where applicable. Passwords are set by Calamares's users
job rather than embedded in the Nix store.

The job runs `nixos-install --flake /TARGET/etc/nixos/kwak-os#physical` with the
appropriate target root and `--no-update-lock-file`. Nix fetches the locked inputs
and missing dependencies normally, using its configured binary caches. Packages
not available in those caches may compile locally. No custom public binary cache
is configured by this change.

Build work and temporary store files go on the destination disk. The installed
root filesystem and bootloader are independent of the USB. Later rebuilds use
`sudo nixos-rebuild switch --flake /etc/nixos/kwak-os#physical`; updating inputs is
an explicit `nix flake update` operation on that saved flake.

## GitHub builds and packages

For a browser download, open the [ISO workflow](https://github.com/hzrd149/kwak-os/actions/workflows/iso.yml),
select a successful run, and download **kwakos-iso-…** under **Artifacts**.
Sign in to GitHub, extract the downloaded ZIP, then flash the `.iso` file inside.
The run summary repeats the USB/UEFI installation instructions.

GitHub Releases has a 2 GiB per-file asset limit. The revised image's size has not
yet been measured; the current workflow keeps the browser artifact and OCI
package download paths, which support larger images.

The [ISO workflow](../.github/workflows/iso.yml) runs **only when a release tag
matching `v*` is pushed**, such as `v0.1.0`. Branch commits and pull requests do
not trigger builds, and manual dispatch is disabled. Each successful tagged build
publishes a commit-tagged OCI image. There is no moving `latest` tag or automatic
GitHub Release creation.

The build job checks the Nix configurations, the built Hyprland configuration,
the settings/installer tests, and the live media's account/storage policy. It also
checks target-configuration preparation and the pinned flake install command.
A separate test boots the production ISO attached
as USB storage and uses OCR to verify that the KwakOS wizard opens automatically.
It builds the ISO, checks BIOS and
UEFI El Torito boot entries, enforces GHCR's layer-size limit, and uploads an
Actions artifact with 90-day retention. The publishing job receives only
that artifact and uses `GITHUB_TOKEN` with `packages: write`. It pushes with
ORAS, downloads by immutable manifest digest, and checks SHA-256 again. The run
summary records the package tag, digest, and exact pull command.

GitHub Packages has no generic ISO package format. Its Container registry
supports OCI artifacts, and [ORAS documents arbitrary-file uploads to GHCR](https://oras.land/docs/compatible_oci_registries/#github-packages-container-registry-ghcr).
The ISO is stored as a file layer, without wrapping it in a runnable container.
GHCR allows 10 GB per layer, and new packages default to private. Authentication,
visibility, and repository linkage are described in the [GitHub Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

For this repository the package is `ghcr.io/hzrd149/kwak-os/iso`. With the
[ORAS CLI](https://oras.land/docs/installation/) installed, download into an
empty directory using the digest from a successful run:

```sh
# Private packages: use a classic PAT with read:packages and package access.
# Supply GHCR_TOKEN through your shell's secret handling, not a literal command.
printf '%s' "$GHCR_TOKEN" | oras login ghcr.io --username YOUR_GITHUB_USER --password-stdin
mkdir kwakos-iso
cd kwakos-iso
oras pull ghcr.io/hzrd149/kwak-os/iso@sha256:MANIFEST_DIGEST_FROM_RUN
sha256sum --check SHA256SUMS
cat SOURCE_REVISION
```

A tag such as `:sha-FULL_COMMIT_SHA` also works; use the digest for an exact
artifact because rerunning the same commit can replace its tag. For public
packages, skip login. `docker pull`/`docker run` are not the download interface
for this artifact. The Actions artifact contains the same ISO, `SHA256SUMS`, and
`SOURCE_REVISION` and is an alternative download while retained.

Publishing errors fail the workflow and leave the build artifact available.
If a package already exists with different repository permissions, its owner
must grant this repository Actions access. Do not add a broad personal token
to bypass package permissions. Package visibility is managed separately; the
workflow does not make private repository artifacts public.

CI validates the build and package round trip, not hardware compatibility.
Before installing on a real machine, test boot and graphics on that hardware.

For release qualification, also boot the actual image attached as USB storage,
verify that exactly one installer opens, complete the guided erase-disk wizard
with networking enabled, and reboot without the USB. Verify that the welcome page
blocks installation without internet. Test legacy-mode rejection
and a non-US keyboard; exercise encrypted and advanced filesystem layouts before
claiming those paths are qualified on hardware.
