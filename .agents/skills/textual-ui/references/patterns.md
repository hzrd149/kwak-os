# Framework patterns and official sources

These notes summarize the rolling documentation reviewed on 2026-10-12. Verify version-specific APIs against the installed package. Repository optimization advice is engineering guidance, not a guarantee made by Textual.

## Compose, layout, and interaction

Use `compose()` for a stable widget hierarchy and `render()` for a widget's visual output. Start with built-in controls instead of recreating input behavior. [Tutorial](https://textual.textualize.io/tutorial/).

Use `Horizontal`/`Vertical` for linear groups, grid for cards/actions, `1fr` for remaining space, and explicit scroll containers for overflow. Check combined minimum widths, borders, and padding at narrow sizes. Keep focusable controls inside visible bounds. [Layout](https://textual.textualize.io/guide/layout/).

Communicate child intent via `Message` and handle service effects in the owning screen/app. Messages can bubble; stop input already authoritatively handled to avoid duplicate actions. Default-handler suppression and bubbling suppression serve different purposes. Audit custom card click/focus handlers so one gesture does not trigger redundant selection work. [Events and messages](https://textual.textualize.io/guide/events/).

## Reactivity

Use reactive state for changes that affect a widget's presentation, and watchers for targeted side effects. Request layout refresh only when dimensions change. Recomposition recreates children and resets their state: use it for small stateless sections, avoid it for frequently changing search fields and large card trees. Prefer stable children with `update()`, class changes, and keyed row updates. [Reactivity](https://textual.textualize.io/guide/reactivity/).

## Workers

Calling an async backend from a message handler and awaiting its completion still delays that handler's message processing. Start a worker and return. Use async workers for truly async APIs and `thread=True` for blocking clients. Assign independent groups so details refresh does not cancel installation. `exclusive=True` replaces earlier work in its group.

Thread workers must marshal UI mutation through `call_from_thread` or post messages. They cannot be forcibly stopped like coroutines: check `get_current_worker().is_cancelled`, bound I/O, and reject results with an obsolete address/generation on the UI thread. Owner removal cancels managed workers, but underlying blocking work may continue. Handle expected failures visibly; if using `exit_on_error=False`, observe failures rather than hiding them. Do not `await worker.wait()` from an interactive handler to regain a blocking flow. [Workers](https://textual.textualize.io/guide/workers/).

Repository-specific rule: snapshot selected address/window IDs before starting a mutation. Latest-wins cancellation is suitable for read-only details; it does not undo install, update, publication, or stop side effects.

See `../scripts/worker-example.py` for a runnable pattern. Resolve `nix eval --raw .#kwak-kiosk.python.outPath`, then run its `bin/python3 .agents/skills/textual-ui/scripts/worker-example.py --self-test`. The async worker awaits `asyncio.to_thread`, so subsequent UI mutations already run on the UI thread. This differs from a `thread=True` worker, which must marshal them. Retain the owning screen and check its mounted state; querying the app's active screen while a settings modal is open can target the wrong DOM. Cancel/invalidate work when its owner is removed. Cancelling the async wrapper does not terminate its underlying blocking thread.

The example bounds request duration but intentionally omits a production queue/concurrency coordinator. For rapid high-volume selection, coalesce pending addresses and limit actual submitted blocking operations; a cancelled wrapper releasing a semaphore while its thread still runs does not enforce that limit.

## Lists

Use stable option IDs and `replace_option_prompt` for status-only changes instead of clearing all options. Restore highlighted address after membership changes. [OptionList](https://textual.textualize.io/widgets/option_list/).

Choose `DataTable` for dense tabular data and update cells by stable row/column keys. [DataTable](https://textual.textualize.io/widgets/data_table/).

For rich cards, profile actual tree size. Fetching artwork only for visible cards does not make the mounted card tree virtualized. Use paging or a bounded/recycled visible-card window if tree construction dominates. Avoid inventing unsupported virtualization APIs.
