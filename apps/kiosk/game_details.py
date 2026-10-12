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
    #store-hero { height: 8; background: $panel; }
    #store-art, #store-fallback { width: 40%; height: 8; }
    #store-fallback { content-align: center middle; color: $primary; text-style: bold; }
    #store-summary { width: 1fr; padding: 1; }
    #store-title { height: auto; color: $primary; text-style: bold; margin-bottom: 1; }
    #store-author, #store-state { height: auto; margin-bottom: 1; }
    #store-description, #store-release, #store-permissions { height: auto; margin: 1 0; }
    #store-actions { height: auto; padding: 0 1; layout: grid; grid-size: 2; grid-gutter: 0 1; }
    #store-actions Button { width: 1fr; height: 3; min-width: 0; margin: 0; }
    #store-tabs { height: 3; padding: 0 1; }
    #store-tabs Button { width: 1fr; min-width: 0; margin: 0; }
    #store-tabs Button.active { background: $primary 12%; color: $primary; text-style: bold; }
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
        self.detail_tab = 'info'
        self.origin = None

    def compose(self):
        yield Button('← Back to games', id='store-back')
        with Horizontal(id='store-hero'):
            yield TerminalImage(id='store-art')
            yield Static('◆', id='store-fallback', markup=False)
            with Vertical(id='store-summary'):
                yield Static('', id='store-title', markup=False)
                yield Static('', id='store-author', markup=False)
                yield Static('', id='store-state', markup=False)
        with Vertical(id='store-actions'):
            yield Button('Play Now', id='play', variant='primary')
            yield Button('Install', id='install')
            yield Button('Update', id='update')
            yield Button('Remove from My Games', id='remove')
        with Horizontal(id='store-tabs'):
            yield Button('Game Info', id='store-info-tab', classes='active')
            yield Button('Releases', id='store-releases-tab')
        with VerticalScroll(id='store-content'):
            yield Static('', id='store-description', markup=False)
            yield Static('', id='store-release', markup=False)
            yield Static('', id='store-permissions', markup=False)
        yield Static('', id='store-feedback', markup=False)

    def on_mount(self):
        self.origin = self.app.page
        self.query_one('#store-back', Button).label = '← Back to My Games' if self.origin == 'installed' else '← Back to Find Games'
        self.show_detail_tab()
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
        pending = bool(app.library and self.address in app.saved and not installed)
        active = app.running.get(self.address, [])
        update = game.get('update_available') or app.installed.get(self.address, {}).get('update_available')
        feedback = str(app.launcher_query_one('#status', Static).content) if self.operation_started else ''
        signature = (game, installed, busy, pending, active, update, self.address in app.saved, feedback, self.error, app.windows_available)
        if signature == self.signature:
            return
        self.signature = signature
        name = game.get('name') or 'Untitled game'
        self.query_one('#store-title', Static).update(name)
        creator = str(game.get('author_name') or game.get('author') or 'Unknown creator')
        if len(creator) == 64 and all(char in '0123456789abcdefABCDEF' for char in creator):
            creator = creator[:8] + '…' + creator[-8:]
        self.query_one('#store-author', Static).update('By ' + creator)
        state = ('Downloading…' if busy or pending else 'Running' if active else 'Installed · Ready to play' if installed else 'Ready to try or install')
        if not game.get('available'):
            state = game.get('unavailable_reason') or 'Unavailable on this system'
        if not app.windows_available:
            state += ' · Running status unavailable'
        self.query_one('#store-state', Static).update(state)
        self.query_one('#store-description', Static).update(game.get('description') or 'No description provided by the creator yet.')
        version = game.get('installed_version') or app.installed.get(self.address, {}).get('version') or game.get('version')
        releases = ('Installed release: ' if installed else 'Release: ') + app.release_label(version)
        if update:
            releases += '\nUpdate available: ' + app.release_label(update)
        if game.get('versions'):
            releases += '\n\nKnown releases\n' + '\n'.join(app.release_label(item) for item in game['versions'])
            releases += '\nRelease selection is not available yet.'
        self.query_one('#store-release', Static).update(releases)
        domains = game.get('required_domains', [])
        self.query_one('#store-permissions', Static).update('Required capabilities: ' + ', '.join(str(domain) for domain in domains) if domains else '')
        image = self.query_one('#store-art', TerminalImage)
        source = artwork({"image": game.get("banner")}) or artwork(game)
        image.display = bool(source)
        self.query_one('#store-fallback').display = not bool(source)
        if source != self.source:
            self.source = source
            image.call_after_refresh(self.apply_artwork, source)
        self.query_one('#store-fallback', Static).update(f'◆\n{name[:2].upper()}')
        self.query_one('#play', Button).label = 'Stop' if active else 'Play' if installed else 'Play Now'
        self.query_one('#play', Button).disabled = busy or (not app.windows_available if active else pending or not game.get('available', False))
        self.query_one('#install', Button).label = 'Installed' if installed else 'Install'
        self.query_one('#install', Button).display = not installed and self.address not in app.saved
        self.query_one('#install', Button).disabled = busy or installed or self.address in app.saved or not game.get('available', False)
        self.query_one('#update', Button).display = bool(update) and installed
        self.query_one('#update', Button).disabled = busy
        self.query_one('#remove', Button).display = self.address in app.saved
        self.query_one('#remove', Button).disabled = busy
        self.query_one('#store-feedback', Static).update(self.error or feedback)

    def on_button_pressed(self, event):
        event.stop()
        if event.button.id in ('store-info-tab', 'store-releases-tab'):
            self.detail_tab = 'info' if event.button.id == 'store-info-tab' else 'releases'
            self.show_detail_tab()
        elif event.button.id == 'store-back':
            self.action_back()
        else:
            self.operation_started = True
            action = 'stop' if event.button.id == 'play' and str(event.button.label) == 'Stop' else event.button.id
            self.app.perform_game_action(action, self.game | self.app.details.get(self.address, {}))
            self.update_page()

    def apply_artwork(self, source):
        if self.is_mounted and self.app.is_running and source == self.source:
            self.query_one('#store-art', TerminalImage).set_source(source)

    def show_detail_tab(self):
        info = self.detail_tab == 'info'
        self.query_one('#store-description').display = info
        self.query_one('#store-permissions').display = info
        self.query_one('#store-release').display = not info
        self.query_one('#store-info-tab', Button).set_class(info, 'active')
        self.query_one('#store-releases-tab', Button).set_class(not info, 'active')

    def action_back(self):
        self.app.pop_screen()
