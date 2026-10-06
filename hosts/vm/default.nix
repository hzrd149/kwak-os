{ modulesPath, pkgs, ... }:
let
  qemuLauncher = pkgs.writeShellScript "kwakos-qemu" ''
    set -euo pipefail
    if [ -e /run/opengl-driver ]; then
      # NixOS supplies its own matching driver set.
      exec ${pkgs.qemu_kvm}/bin/qemu-system-x86_64 "$@"
    elif [ -f /usr/share/glvnd/egl_vendor.d/10_nvidia.json ] &&
         [ -f /usr/lib/x86_64-linux-gnu/libEGL_nvidia.so.0 ]; then
      # Never add the whole host library directory: its libc is incompatible
      # with the Nix dynamic loader. Expose only NVIDIA's driver libraries.
      driverDir=$(${pkgs.coreutils}/bin/mktemp -d "''${TMPDIR:-/tmp}/kwakos-nvidia.XXXXXXXX")
      trap '${pkgs.coreutils}/bin/rm -f "$driverDir"/*; ${pkgs.coreutils}/bin/rmdir "$driverDir"' EXIT
      for library in /usr/lib/x86_64-linux-gnu/libnvidia*.so* \
                     /usr/lib/x86_64-linux-gnu/libEGL_nvidia.so* \
                     /usr/lib/x86_64-linux-gnu/libGLX_nvidia.so*; do
        [ -e "$library" ] || continue
        ${pkgs.coreutils}/bin/ln -s "$library" "$driverDir/''${library##*/}"
      done
      export __EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/10_nvidia.json
      export __GLX_VENDOR_LIBRARY_NAME=nvidia
      export LD_LIBRARY_PATH="$driverDir''${LD_LIBRARY_PATH:+:''${LD_LIBRARY_PATH}}"
      export GBM_BACKEND=nvidia-drm
      export GBM_BACKENDS_PATH=/usr/lib/x86_64-linux-gnu/gbm
      ${pkgs.qemu_kvm}/bin/qemu-system-x86_64 "$@"
    else
      export LIBGL_ALWAYS_SOFTWARE=true
      export GALLIUM_DRIVER=llvmpipe
      export LIBGL_DRIVERS_PATH="${pkgs.mesa}/lib/dri"
      export __EGL_VENDOR_LIBRARY_FILENAMES="${pkgs.mesa}/share/glvnd/egl_vendor.d/50_mesa.json"
      exec ${pkgs.qemu_kvm}/bin/qemu-system-x86_64 "$@"
    fi
  '';
in
{
  imports = [ (modulesPath + "/virtualisation/qemu-vm.nix") ];

  networking.hostName = "kwakos-vm";

  # This credential is confined to the disposable local VM.
  users.users.kwak.initialPassword = "nixos";

  virtualisation = {
    memorySize = 4096;
    cores = 4;
    diskSize = 20480;
    graphics = true;
    # Nix-built GL apps do not automatically discover host distro drivers.
    # Pick the best available source:
    # NixOS system drivers, a host distro NVIDIA driver, or CPU rendering.
    qemu.package = pkgs.symlinkJoin {
      name = "qemu-kwakos-portable";
      paths = [ pkgs.qemu_kvm ];
      postBuild = ''
        ln -sf ${qemuLauncher} "$out/bin/qemu-system-x86_64"
      '';
    };
    qemu.options = [
      "-vga none"
      "-device virtio-vga-gl"
      # SDL's GL context works with the proprietary NVIDIA host driver;
      # QEMU's GTK EGL path fails with EGL_BAD_ALLOC on this setup.
      "-display sdl,gl=on"
    ];
  };
}
