# Live ISO

kwakOS uses the ISO builder already included in its locked Nixpkgs input. The
`iso` host imports NixOS's `installation-cd-base.nix` alongside the same base and
desktop modules as the VM and physical hosts. No new flake input is required.
NixOS documents this approach in [Building a NixOS (Live) ISO](https://nixos.org/manual/nixos/stable/#sec-building-cd).

## Build

On x86_64 Linux with Nix and flakes enabled:

```sh
nix build .#iso --out-link result-iso
nix build .#checks.x86_64-linux.desktop-config .#checks.x86_64-linux.iso-config --no-link
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
reboot. It does not modify disks unless you explicitly run installation tools.

SDDM logs into the desktop as `kwak`. NixOS's installer profile also provides
the `nixos` console account and `root`. These live accounts have empty passwords
and wheel users have passwordless sudo; SSH is disabled. These settings belong
only to `hosts/iso` and do not change the physical host's password policy.

Boot as a virtual CD/DVD or write the ISO to a USB drive using an image writer.
Writing an image replaces the selected drive's contents. The media supports
BIOS and UEFI; Secure Boot and physical GPU compatibility have not been qualified.
The physical installation target still requires UEFI and the hardware setup
described in the [installation instructions](../README.md#install-on-a-physical-machine).
This is a live desktop with manual installation tools, without a graphical or
unattended disk installer. Network access is needed to obtain this repository
and any additional installation dependencies.

## GitHub builds and packages

The [ISO workflow](../.github/workflows/iso.yml) runs on branch pushes, `v*` tags,
and manual dispatch. Same-repository PRs use their existing push build; fork PRs
build with read-only permissions and cannot publish packages. Every trusted
push or manual run publishes a commit-tagged candidate, including feature
branches. There is no moving `latest` tag or automatic release promotion.

The build job checks the Nix configurations, the built Hyprland configuration,
and the live media's account/storage policy. It builds the ISO, checks BIOS and
UEFI El Torito boot entries, enforces GHCR's layer-size limit, and uploads an
Actions artifact with seven-day retention. The publishing job receives only
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
