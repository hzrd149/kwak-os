{
  lib,
  stdenvNoCC,
  python3,
  callPackage,
  makeWrapper,
  # The nostr-swipe-cards source, from the flake input.
  skcSource,
}:
let
  skc = callPackage ./skc.nix { source = skcSource; };
  python = python3.withPackages (_: [ skc ]);
in
stdenvNoCC.mkDerivation {
  pname = "kwak-cards";
  version = "0.1.0";
  src = ./.;

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [ python ];
  dontBuild = true;
  doCheck = true;

  checkPhase = ''
    runHook preCheck
    python3 -m unittest discover -s tests
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 cards.py $out/share/kwak/cards.py
    makeWrapper ${python}/bin/python3 $out/bin/kwak-cards \
      --add-flags "$out/share/kwak/cards.py"
    runHook postInstall
  '';

  passthru = { inherit python skc; };

  meta = {
    description = "Passes Nostr swipe card reads from an MSR90 reader to kwak-userd";
    mainProgram = "kwak-cards";
    platforms = lib.platforms.linux;
  };
}
