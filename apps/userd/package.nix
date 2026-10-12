{
  doCheck ? false,
  lib,
  stdenvNoCC,
  python3,
  makeWrapper,
  nak,
  shadow,
  systemd,
  procps,
  findutils,
  kbd,
}:
let
  # pynacl for NIP-49; coincurve and cryptography to sign for kwakore;
  # python-pam to check kept accounts' Unix passwords.
  pythonPackages = ps: [
    ps.pynacl
    ps.coincurve
    ps.cryptography
    ps.python-pam
    ps.websocket-client
    ps.pillow
  ];
  python = python3.withPackages pythonPackages;
  testPython = python3.withPackages (ps: pythonPackages ps ++ [ ps.websockets ]);
in
stdenvNoCC.mkDerivation {
  pname = "kwak-userd";
  version = "0.1.0";
  src = ./.;

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [
    testPython
    nak
  ];
  dontBuild = true;
  inherit doCheck;

  checkPhase = ''
    runHook preCheck
    python3 -m unittest discover -s tests
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 userd.py $out/share/kwak/userd.py
    makeWrapper ${python}/bin/python3 $out/bin/kwak-userd \
      --add-flags "$out/share/kwak/userd.py" \
      --set KWAK_USERD $out/bin/kwak-userd \
      --prefix PATH : ${
        lib.makeBinPath [
          nak
          shadow
          systemd
          procps
          findutils
          kbd
        ]
      }
    runHook postInstall
  '';

  passthru = { inherit python; };

  meta = {
    description = "Nostr identity user manager for kwakOS";
    mainProgram = "kwak-userd";
    platforms = lib.platforms.linux;
  };
}
