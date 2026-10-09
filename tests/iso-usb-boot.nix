# Boot the production ISO as a USB mass-storage device, not as a virtual CD.
# No test backdoor is added to the ISO: observe the actual wizard through QMP/OCR.
{ pkgs, iso }:
pkgs.testers.runNixOSTest {
  name = "kwak-iso-usb-boot";
  enableOCR = true;
  nodes.usb = { lib, ... }: {
    virtualisation = {
      memorySize = 4096;
      cores = 2;
      graphics = true;
      diskImage = null;
      emptyDiskImages = [ (32 * 1024) ];
      useBootLoader = true;
      useEFIBoot = true;
      useDefaultFilesystems = false;
      efi.keepVariables = false;
      qemu.networkingOptions = lib.mkForce [ "-nic none" ];
      qemu.options = [
        "-vga none"
        "-device virtio-vga,xres=1280,yres=800"
        "-device qemu-xhci,id=installer-xhci"
        "-drive if=none,id=installer-usb,file=${iso}/iso/${iso.isoName},format=raw,readonly=on"
        "-device usb-storage,bus=installer-xhci.0,drive=installer-usb,bootindex=1"
      ];
    };
    # This node's generated system is not booted; the USB supplies the system.
    virtualisation.fileSystems."/" = {
      device = "/dev/disk/by-label/not-used";
      fsType = "ext4";
    };
  };
  testScript = ''
    usb.start()
    usb.wait_for_text("Welcome to the KwakOS installer", timeout=360)
    usb.screenshot("kwakos-usb-installer")
  '';
}
