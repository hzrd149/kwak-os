"""Last non-destructive check, executed before Calamares partitions any disk."""
import os
import subprocess
import sys
sys.path.insert(0, "@KWAK_MODULE@")
import kwak_installer
import libcalamares


def disks(device):
    output = subprocess.check_output(
        ["lsblk", "--inverse", "--noheadings", "--paths", "--output", "NAME,TYPE", device],
        text=True,
    )
    return {fields[0] for line in output.splitlines()
            if len(fields := line.split()) == 2 and fields[1] == "disk"}


def run():
    if not os.path.isdir("/sys/firmware/efi"):
        return ("KwakOS requires UEFI",
                "No disks have been changed. Restart and select the UEFI entry for your USB stick.")
    storage = libcalamares.globalstorage
    try:
        kwak_installer.settings(storage, {})
    except ValueError as error:
        return ("Check your local account", str(error))
    partitions = storage.value("partitions") or []
    if os.path.ismount("/iso"):
        try:
            source = subprocess.check_output(
                ["findmnt", "--noheadings", "--output", "SOURCE", "/iso"], text=True,
            ).strip()
            # With copytoram, /iso is a tmpfs and no longer depends on the USB.
            source_disks = disks(source) if source.startswith("/dev/") else set()
            for part in partitions:
                if part.get("claimed") and part.get("device") and source_disks & disks(part["device"]):
                    return ("Choose a different destination disk",
                            "The selected disk contains the running USB installer. No disks have been changed.")
        except (OSError, subprocess.CalledProcessError):
            return ("Could not verify the destination disk",
                    "No disks have been changed. Reopen the installer and check the selected disk.")
    if not any(part.get("mountPoint") == "/boot" and part.get("fs") == "fat32"
               for part in partitions):
        # Calamares/kpmcore may report an existing FAT ESP as vfat or fat16.
        if not any(part.get("mountPoint") == "/boot" and part.get("fs") in ("vfat", "fat16")
                   for part in partitions):
            return ("An EFI system partition is required",
                    "Use the guided erase-disk layout, or mount a FAT EFI system partition at /boot.")
    return None
