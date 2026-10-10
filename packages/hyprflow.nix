{
  lib,
  hyprland,
  fetchFromGitHub,
  pkg-config,
}:
hyprland.stdenv.mkDerivation {
  pname = "hyprflow";
  version = "0-unstable-2026-10-10";

  src = fetchFromGitHub {
    owner = "sandwichfarm";
    repo = "hyprflow";
    rev = "b4de91659bb82a9e283fbc6ff542eb83a7ee8bba";
    hash = "sha256-dJO9kkcim/xHmo4VXAZRH9e0XsVnlpF/frJmA8l0+6c=";
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
