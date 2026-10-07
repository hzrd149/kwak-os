{
  calamares-nixos-extensions,
  kwakSource,
  kwakInputs,
}:
calamares-nixos-extensions.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./calamares-kwakos.patch ];

  postPatch = (old.postPatch or "") + ''
    substituteInPlace modules/nixos/main.py \
      --replace-fail '@KWAK_SOURCE@' '${kwakSource}' \
      --replace-fail '@KWAK_INPUTS@' '${kwakInputs}' \
      --replace-fail '@KWAK_MODULE@' "$out/lib/calamares/modules/nixos"
    cp ${./installer/kwak_installer.py} modules/nixos/kwak_installer.py
    substituteInPlace modules/nixos/main.py \
      --replace-fail 'Installing NixOS' 'Installing KwakOS' \
      --replace-fail 'Configuring NixOS' 'Configuring KwakOS' \
      --replace-fail 'Generating NixOS configuration' 'Generating KwakOS configuration'
  '';

  postInstall = (old.postInstall or "") + ''
    cp ${../config/calamares/settings.conf} $out/etc/calamares/settings.conf
    substituteInPlace $out/etc/calamares/settings.conf \
      --replace-fail '@out@' "$out"
    cp ${../config/calamares/welcome.conf} $out/etc/calamares/modules/welcome.conf
    cp ${../config/calamares/users.conf} $out/etc/calamares/modules/users.conf
    cp ${../config/calamares/partition.conf} $out/etc/calamares/modules/partition.conf
    cp ${../config/calamares/finished.conf} $out/etc/calamares/modules/finished.conf
    mkdir -p $out/share/calamares/branding/kwakos
    cp ${../config/calamares/branding.desc} $out/share/calamares/branding/kwakos/branding.desc
    cp ${../config/calamares/welcome.svg} $out/share/calamares/branding/kwakos/welcome.svg
    mkdir -p $out/lib/calamares/modules/kwakcheck
    cp ${./installer/preflight.py} $out/lib/calamares/modules/kwakcheck/main.py
    substituteInPlace $out/lib/calamares/modules/kwakcheck/main.py \
      --replace-fail '@KWAK_MODULE@' "$out/lib/calamares/modules/nixos"
    cat > $out/lib/calamares/modules/kwakcheck/module.desc <<'EOF'
    ---
    type: job
    name: kwakcheck
    interface: python
    script: main.py
    EOF
  '';
})
