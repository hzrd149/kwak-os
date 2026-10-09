{
  lib,
  hyprland,
  fetchFromGitHub,
  pkg-config,
}:
hyprland.stdenv.mkDerivation {
  pname = "hyprflow";
  version = "0-unstable-2026-10-06";

  src = fetchFromGitHub {
    owner = "sandwichfarm";
    repo = "hyprflow";
    rev = "2607d5de36902f8d770679bb40d72fa4a240c85c";
    hash = "sha256-0KGzk6jNpVW8oxLPSeUrsiS27m2+X7/yhHSgSYVoZQM=";
  };

  nativeBuildInputs = [ pkg-config ];
  # Plugins need the compositor's exact compiler, headers, and dependency ABI.
  buildInputs = [ hyprland ] ++ hyprland.buildInputs;
  enableParallelBuilding = true;
  # The pinned compositor uses Lua 5.5; upstream's Makefile defaults to 5.4.
  preBuild = ''
    makeFlagsArray+=("PKGS=hyprland lua egl glesv2 pangocairo")
  '';

  doCheck = true;
  checkTarget = "test";

  installPhase = ''
    runHook preInstall
    install -Dm755 build/hyprflow.so $out/lib/hyprflow.so
    install -Dm644 config/hyprflow.lua $out/share/hyprflow/hyprflow.lua
    runHook postInstall
  '';

  meta = {
    description = "Cover Flow workspace switcher for Hyprland";
    homepage = "https://github.com/sandwichfarm/hyprflow";
    platforms = lib.platforms.linux;
  };
}
