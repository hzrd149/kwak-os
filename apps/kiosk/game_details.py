"""Dedicated Napplet store page, sharing the launcher's daemon operations."""
import asyncio

from textual import work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Static

from discovery import artwork
from images import TerminalImage


class GameDetails(Screen):
    BINDINGS = [Binding('escape', 'back', 'Back to games', priority=True)]
    CSS = """
    GameDetails { background: $background; }
    #store-back { height: 3; width: auto; min-width: 24; margin: 0; }
    #store-content { height: 1fr; padding: 0 1; }
    #store-hero { height: 10; background: $panel; }
    #store-art, #store-fallback { width: 40%; height: 10; }
    #store-fallback { content-align: center middle; color: $primary; text-style: bold; }
    #store-summary { width: 1fr; padding: 1; }
    #store-title { height: auto; color: $primary; text-style: bold; margin-bottom: 1; }
    #store-author, #store-state { height: auto; margin-bottom: 1; }
    #store-description, #store-release, #store-permissions { height: auto; margin: 1 0; }
    #store-actions { height: auto; padding: 0 1; layout: grid; grid-size: 2; grid-gutter: 0 1; }
    #store-actions Button { width: 1fr; height: 3; min-width: 0; margin: 0; }
    #store-feedback { height: auto; padding: 0 1; color: $warning; }
    """

    def __init__(self, game):
        super().__init__()
        self.game = dict(game)
        self.address = game['address']
        self.error = ''
        self.signature = None
        self.source = None
        self.operation_started = False

    def compose(self):
        yield Button('← Back to Find Games', id='store-back')
        with VerticalScroll(id='store-content'):
            with Horizontal(id='store-hero'):
                yield TerminalImage(id='store-art')
                yield Static('◆', id='store-fallback', markup=False)
                with Vertical(id='store-summary'):
                    yield Static('', id='store-title', markup=False)
                    yield Static('', id='store-author', markup=False)
                    yield Static('', id='store-state', markup=False)
            yield Static('', id='store-description', markup=False)
            yield Static('', id='store-release', markup=False)
            yield Static('', id='store-permissions', markup=False)
        with Vertical(id='store-actions'):
            yield Button('Play Now', id='play', variant='primary')
            yield Button('Install', id='install')
            yield Button('Stop', id='stop')
            yield Button('Update', id='update')
            yield Button('Remove from My Games', id='remove')
        yield Static('', id='store-feedback', markup=False)

    def on_mount(self):
        self.update_page()
        self.query_one('#store-back', Button).focus()
        self.read_details()
        self.set_interval(1, self.update_page)
        self.set_interval(15, self.retry_details)

    def retry_details(self):
        if not any(worker.node is self and not worker.is_finished for worker in self.workers):
            self.read_details()

    @work(exclusive=True, group='store-detail')
    async def read_details(self):
        if not callable(getattr(self.app.runtime, 'details', None)):
            return
        try:
            detail = await asyncio.to_thread(self.app.runtime.details, self.address)
        except RuntimeError:
            self.error = 'Some game details are temporarily unavailable. Retrying automatically.'
        else:
            self.game.update(detail)
            self.app.details[self.address] = detail
            self.error = ''
        if self.is_mounted:
            self.update_page()

    def update_page(self):
        app = self.app
        game = self.game | app.details.get(self.address, {})
        installed = self.address in app.installed
        busy = self.address in app.busy
        active = app.running.get(self.address, [])
        update = game.get('update_available') or app.installed.get(self.address, {}).get('update_available')
        feedback = str(app.launcher_query_one('#status', Static).content) if self.operation_started else ''
        signature = (game, installed, busy, active, update, self.address in app.saved, feedback, self.error)
        if signature == self.signature:
            return
        self.signature = signature
        name = game.get('name') or 'Untitled game'
        self.query_one('#store-title', Static).update(name)
        self.query_one('#store-author', Static).update('By ' + (game.get('author_name') or game.get('author') or 'Unknown creator'))
        state = ('Downloading…' if busy else 'Running' if active else 'Installed · Ready to play' if installed else 'Ready to try or install')
        if not game.get('available'):
            state = game.get('unavailable_reason') or 'Unavailable on this system'
        self.query_one('#store-state', Static).update(state)
        self.query_one('#store-description', Static).update(game.get('description') or 'No description provided by the creator yet.')
        self.query_one('#store-release', Static).update('Release: ' + app.release_label(game.get('version')))
        domains = game.get('required_domains', [])
        self.query_one('#store-permissions', Static).update('Required capabilities: ' + ', '.join(str(domain) for domain in domains) if domains else '')
        image = self.query_one('#store-art', TerminalImage)
        source = artwork(game)
        image.display = bool(source)
        self.query_one('#store-fallback').display = not bool(source)
        if source != self.source:
            self.source = source
            image.set_source(source)
        self.query_one('#store-fallback', Static).update(f'◆\n{name[:2].upper()}')
        self.query_one('#play', Button).label = 'Play' if installed else 'Play Now'
        self.query_one('#play', Button).disabled = busy or not game.get('available', False)
        self.query_one('#install', Button).label = 'Installed' if installed else 'Install'
        self.query_one('#install', Button).disabled = busy or installed or self.address in app.saved or not game.get('available', False)
        self.query_one('#stop', Button).display = bool(active)
        self.query_one('#stop', Button).disabled = busy
        self.query_one('#update', Button).display = bool(update) and installed
        self.query_one('#update', Button).disabled = busy
        self.query_one('#remove', Button).display = self.address in app.saved
        self.query_one('#remove', Button).disabled = busy
        self.query_one('#store-feedback', Static).update(self.error or feedback)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id == 'store-back':
            self.action_back()
        else:
            self.operation_started = True
            self.app.perform_game_action(event.button.id, self.game | self.app.details.get(self.address, {}))
            self.update_page()

    def action_back(self):
        self.app.pop_screen()
