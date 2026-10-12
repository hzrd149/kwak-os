{ modulesPath, pkgs, ... }:
{
  imports = [ (modulesPath + "/virtualisation/qemu-vm.nix") ];

  networking.hostName = "kwakos-vm";

  # QEMU's GTK window reports its initial 640x480 size as the guest's
  # preferred mode, overriding xres/yres; request a 4:3 mode explicitly.
  kwak.monitorMode = "1600x1200";
  # A predictable 1280x960 logical desktop keeps terminal controls readable
  # when the host scales the QEMU window down to fit its display.
  kwak.monitorScale = "1.25";
  boot.kernelParams = [ "video=Virtual-1:1600x1200" ];

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
      "-device virtio-vga-gl,xres=1600,yres=1200"
      "-display gtk,gl=on"
    ];
  };
}
