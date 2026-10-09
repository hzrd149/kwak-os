{
  lib,
  python3Packages,
  source,
}:
# Only the library: codecs, NIP-49, nbunksec, and the native MSR90 reader. The
# MSR605X and TUI dependencies are left out.
python3Packages.buildPythonPackage {
  pname = "nostr-swipe-cards";
  version = "0.1.0-${source.shortRev or "dirty"}";
  pyproject = true;
  src = source;

  build-system = [ python3Packages.hatchling ];
  dependencies = [ python3Packages.pynacl ];
  nativeCheckInputs = [ python3Packages.pytestCheckHook ];
  # Tests of the TUI and its NIP-44/46 pairing need the TUI's dependencies.
  disabledTestPaths = [
    "tests/test_nip44.py"
    "tests/test_nip46.py"
    "tests/test_tui.py"
  ];
  pythonImportsCheck = [
    "skc_cards"
    "skc_cards.nbunksec"
    "skc_cards.msr90"
  ];

  meta = {
    description = "Read and write Nostr magnetic-stripe key cards";
    mainProgram = "skc";
    platforms = lib.platforms.linux;
  };
}
