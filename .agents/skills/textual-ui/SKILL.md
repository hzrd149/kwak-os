---
name: textual-ui
description: Build, review, and optimize Python Textual terminal interfaces in kwakOS. Use for launcher, discovery cards, library, overlay, settings, greeter, keyboard or mouse navigation, slow rendering, background workers, terminal images, and Pilot tests. Apply even when the user describes a TUI feature or performance problem without naming Textual.
compatibility: Python and the repository's Nix-packaged Textual environment; optional textual-dev tools for interactive debugging.
---

# Textual UI engineering

Build the smallest useful interface, preserve user state, and measure responsiveness before choosing optimizations. Framework concurrency does not make blocking operations or large widget trees inexpensive.

## Start with the local contract

Read the applicable `AGENTS.md`, `docs/kiosk.md`'s **Launcher style and interaction contract**, and the nearest existing widget/tests. Read [repo conventions](references/repo.md) for entry points, theme, image integration, and safe verification commands. User instructions take precedence over the recorded design contract.

Confirm the packaged Textual version before adopting APIs from the rolling website. The researched environment had Textual **8.2.6** and textual-image **0.12.0** on 2026-10-12; these are observations, not dependency requirements. Verify with `importlib.metadata.version` using the package's Python interpreter. Read [framework patterns](references/patterns.md) when designing widgets or concurrency, and [performance and testing](references/performance.md) when investigating latency or adding dynamic content.

## Implement a vertical slice

1. Define the user's action and observable result, including unavailable service, empty, loading, and failure states. Keep service operations in the existing kwakore/runtime boundary.
2. Compose a stable shell with built-in widgets and simple containers. Keep stateful search, lists, and details mounted across refreshes. Give reusable widgets semantic messages; let the app own service actions.
3. Use the shared theme variables. Match compact navigation, readable controls, focus feedback, and mouse/keyboard parity. Distinguish selection from explicit activation according to the current page's contract.
4. Keep message handlers short. Start workers rather than waiting for backend operations inside handlers. Bound requests, concurrency, and retry behavior; marshal thread results back to the UI thread.
5. Update the smallest changed region. Key selection by stable game address, preserve focus/search/scroll, and skip unchanged data. Avoid replacing every row/card for status-only changes.
6. For artwork, reuse `TerminalImage` and `ImageCache`; start loads near the visible viewport and retain fallback labels. Never put fetch/decode/protocol probes in `render()`.
7. Test the user flow and delayed/failing backend paths. Measure cold-load, navigation, filtering, and idle behavior separately. Report evidence and limitations; a headless test does not prove graphical protocol or VM rendering quality.

## Review before finishing

- No blocking subprocess, disk, network, or image decode on the UI loop.
- Async handlers do not wait through long operations merely because they use `await`.
- Stale results cannot replace the latest selection; cancelling a worker is not proof that its underlying thread or process stopped.
- Timers cannot create unbounded overlap; unchanged polls do not rebuild the interface.
- Small-terminal controls remain reachable; keyboard and mouse actions preserve context.
- Performance claims include workload, environment, before/after measurements, and backend call/widget counts where useful.

Use [evaluation prompts](evals/evals.json) to assess future changes to this skill. They are scenarios with review assertions, not an executed benchmark or a promise of perfect performance.

For correct worker syntax, run/read [the executable example](scripts/worker-example.py). Its `--self-test` exercises responsive input, replacement details, and delivery beneath a settings modal with a bounded fake backend; it touches no real games.
