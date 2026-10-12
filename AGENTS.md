# Repository skills

For building, reviewing, or optimizing Python Textual terminal interfaces, read
[the Textual UI skill](.agents/skills/textual-ui/SKILL.md). It includes the
repository's shared UI conventions, concurrency patterns, performance checks,
and links to the official documentation. Read the relevant reference files
linked by the skill for the task at hand.

For launcher changes, also follow [apps/kiosk/AGENTS.md](apps/kiosk/AGENTS.md)
and the launcher style and interaction contract in [docs/kiosk.md](docs/kiosk.md).

Use the Python environment exposed by `.#kwak-kiosk.python` to verify Textual
code against the version packaged by this flake. Preserve unrelated working
tree changes when making fixes.

Repository test suites run only through explicit flake check targets or direct
test commands. Normal packages default to `doCheck = false`; do not attach
automatic test execution to `nix run`, ordinary builds, or system rebuilds.
