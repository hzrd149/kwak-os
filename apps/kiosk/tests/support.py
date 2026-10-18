"""Shared Pilot helpers for launcher tests."""


async def settle(pilot):
    """Workers start further workers (card rebuilds); wait for all of them."""
    for _ in range(5):
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()


async def show_grid(pilot):
    """Switch the current page from its home shelves to the cover grid."""
    app = pilot.app
    await pilot.click("#library-all" if app.page == "installed" else "#store-browse")
    await settle(pilot)
