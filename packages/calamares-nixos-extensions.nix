{
  calamares-nixos-extensions,
  kwakSource,
}:
calamares-nixos-extensions.overrideAttrs (old: {
  patches = (old.patches or [ ]) ++ [ ./calamares-kwakos.patch ];

  postPatch = (old.postPatch or "") + ''
    substituteInPlace modules/nixos/main.py \
      --replace-fail '@KWAK_SOURCE@' '${kwakSource}'
  '';

  postInstall = (old.postInstall or "") + ''
    cp ${../config/calamares/settings.conf} $out/etc/calamares/settings.conf
    substituteInPlace $out/etc/calamares/settings.conf \
      --replace-fail '@out@' "$out"
  '';
})
