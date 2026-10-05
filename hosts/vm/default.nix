{ modulesPath, pkgs, ... }:
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
    # Nix GL applications cannot automatically use Ubuntu/Fedora's host drivers.
    # Supply a matching Mesa EGL implementation and render virgl on the CPU.
    qemu.package = pkgs.symlinkJoin {
      name = "qemu-kwakos-portable";
      paths = [ pkgs.qemu_kvm ];
      nativeBuildInputs = [ pkgs.makeWrapper ];
      postBuild = ''
        wrapProgram "$out/bin/qemu-system-x86_64" \
          --set LIBGL_ALWAYS_SOFTWARE true \
          --set GALLIUM_DRIVER llvmpipe \
          --set LIBGL_DRIVERS_PATH "${pkgs.mesa}/lib/dri" \
          --set __EGL_VENDOR_LIBRARY_FILENAMES "${pkgs.mesa}/share/glvnd/egl_vendor.d/50_mesa.json"
      '';
    };
    qemu.options = [
      "-vga none"
      "-device virtio-vga-gl"
      "-display gtk,gl=on"
    ];
  };
}
