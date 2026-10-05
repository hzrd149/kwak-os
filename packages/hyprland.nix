{
  hyprland,
  glaze,
  fetchFromGitHub,
}:
# The v0.56.2 lock supplies Glaze 8, but this release requires Glaze 7.
# Supply its declared fallback version without CMake fetching during the build.
hyprland.override {
  glaze-hyprland =
    (glaze.override {
      enableSSL = false;
      enableInterop = false;
    }).overrideAttrs
      {
        version = "7.2.0";
        src = fetchFromGitHub {
          owner = "stephenberry";
          repo = "glaze";
          tag = "v7.2.0";
          hash = "sha256-f3NVRi3SXKo42hn0WCw7JsOK3EkdOVJIcuzhPorKjFY=";
        };
      };
}
