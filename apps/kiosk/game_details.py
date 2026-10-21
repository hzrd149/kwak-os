"""Dedicated Napplet store page, sharing the launcher's daemon operations."""
import asyncio

from textual import work
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Static

from discovery import artwork, card_source
from hints import ButtonHints
from images import TerminalImage
from navigation import ConfirmPress

TAB_LABELS = {'info': 'Game Info', 'releases': 'Releases', 'snapshots': 'Snapshots', 'permissions': 'Permissions'}


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
    .store-list { height: auto; }
    .store-list Button { width: 100%; height: 1; border: none; min-width: 0; margin: 0; padding: 0 1; content-align: left middle; }
    .store-list .list-note { height: auto; color: $foreground 70%; margin: 1 0; }
    .rule-row { height: 1; }
    .rule-row Static { width: 1fr; }
    .rule-row Button { width: auto; }
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
        self.icon_missing = False
        self.tab_data = {}
        self.rules = []
        self.notice = ''
        self.confirm = ConfirmPress()

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
            yield Button('☆ Favourite', id='favorite')
            yield Button('Options…', id='options')
        with Horizontal(id='store-tabs'):
            for tab, label in TAB_LABELS.items():
                yield Button(label, id=f'store-{tab}-tab', classes='active' if tab == 'info' else '')
        with VerticalScroll(id='store-content'):
            yield Static('', id='store-description', markup=False)
            yield Static('', id='store-release', markup=False)
            yield Static('', id='store-permissions', markup=False)
            yield Vertical(id='store-versions', classes='store-list')
            yield Vertical(id='store-snapshots', classes='store-list')
            yield Vertical(id='store-rules', classes='store-list')
        yield Static('', id='store-feedback', markup=False)
        yield ButtonHints()

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
        favorite = app.activity.favorite(self.address)
        signature = (game, installed, busy, pending, active, update, self.address in app.saved, feedback, self.error,
                     self.notice, app.windows_available, favorite)
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
        if installed:
            releases += '\nChoose a release below to switch to it. Switching resets this game\'s saved data and settings.'
        self.query_one('#store-release', Static).update(releases)
        self.query_one('#store-permissions-tab').display = installed
        if not installed and self.detail_tab == 'permissions':
            self.detail_tab = 'info'
            self.show_detail_tab()
        domains = game.get('required_domains', [])
        self.query_one('#store-permissions', Static).update('Required capabilities: ' + ', '.join(str(domain) for domain in domains) if domains else '')
        image = self.query_one('#store-art', TerminalImage)
        source = artwork({"image": game.get("banner")}) or card_source(game)
        if source != self.source:
            self.source = source
            self.icon_missing = False
            image.call_after_refresh(self.apply_artwork, source)
        shown = bool(source) and not self.icon_missing
        image.display = shown
        self.query_one('#store-fallback').display = not shown
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
        self.query_one('#favorite', Button).label = '★ Favourite' if favorite else '☆ Favourite'
        self.query_one('#store-feedback', Static).update(self.error or self.notice or feedback)

    def on_button_pressed(self, event):
        event.stop()
        button = event.button
        if button.id and button.id.startswith('store-') and button.id.endswith('-tab'):
            self.detail_tab = button.id[len('store-'):-len('-tab')]
            self.show_detail_tab()
        elif button.has_class('version'):
            self.choose_release(button)
        elif button.has_class('snapshot'):
            self.operation_started = True
            self.app.perform_game_action('play', {'address': button.name, 'available': True})
        elif button.has_class('rule'):
            permission, subject, decision = self.rules[int(button.name)]
            self.change_permission(permission, subject, str(button.label).lower())
        elif event.button.id == 'store-back':
            self.action_back()
        else:
            self.operation_started = True
            action = 'stop' if event.button.id == 'play' and str(event.button.label) == 'Stop' else event.button.id
            self.app.perform_game_action(action, self.game | self.app.details.get(self.address, {}))
            self.update_page()

    def apply_artwork(self, source):
        if self.is_mounted and self.app.is_running and source == self.source:
            if source.startswith('kwak-icon:'):
                self.load_icon(source)
            else:
                self.query_one('#store-art', TerminalImage).set_source(source)

    @work(exclusive=True, group='store-icon')
    async def load_icon(self, source):
        icons = getattr(self.app, 'icons', None)
        path = await icons.load(self.game | self.app.details.get(self.address, {})) if icons is not None else None
        if not self.is_mounted or source != self.source:
            return
        if path is None:
            self.icon_missing = True
            self.signature = None
            self.update_page()
        else:
            self.query_one('#store-art', TerminalImage).set_source(str(path))

    def show_detail_tab(self):
        tab = self.detail_tab
        self.query_one('#store-description').display = tab == 'info'
        self.query_one('#store-permissions').display = tab in ('info', 'permissions')
        self.query_one('#store-release').display = tab == 'releases'
        self.query_one('#store-versions').display = tab == 'releases'
        self.query_one('#store-snapshots').display = tab == 'snapshots'
        self.query_one('#store-rules').display = tab == 'permissions'
        for name in TAB_LABELS:
            self.query_one(f'#store-{name}-tab', Button).set_class(name == tab, 'active')
        if tab != 'info':
            self.load_tab(tab)

    def tabs(self):
        return [tab for tab in TAB_LABELS if self.query_one(f'#store-{tab}-tab').display]

    def cycle_tab(self, delta):
        tabs = self.tabs()
        current = tabs.index(self.detail_tab) if self.detail_tab in tabs else 0
        self.detail_tab = tabs[(current + delta) % len(tabs)]
        self.show_detail_tab()
        return True

    # Releases, snapshots and permissions ---------------------------------------

    @work(exclusive=True, group='store-tab')
    async def load_tab(self, tab):
        runtime = self.app.runtime
        reader = {'releases': 'versions', 'snapshots': 'snapshots', 'permissions': 'permissions'}[tab]
        if not callable(getattr(runtime, reader, None)):
            data = None
        else:
            try:
                data = await asyncio.to_thread(getattr(runtime, reader), self.address)
            except RuntimeError:
                data = self.tab_data.get(tab, False)  # Keep what was shown; note the failure.
        if not self.is_mounted:
            return
        self.tab_data[tab] = data
        await {'releases': self.show_releases, 'snapshots': self.show_snapshots,
               'permissions': self.show_rules}[tab](data)

    async def replace_list(self, selector, widgets):
        container = self.query_one(selector)
        await container.remove_children()
        await container.mount(*widgets)

    def note(self, text):
        return Static(text, classes='list-note', markup=False)

    async def show_releases(self, page):
        if not page:
            await self.replace_list('#store-versions', [self.note(
                'Release history is temporarily unavailable.' if page is False else 'No release history is known yet.')])
            return
        installed = self.address in self.app.installed
        latest = (page.get('latest_observed') or {}).get('event_id')
        rows = []
        for index, item in enumerate(page.get('items', [])):
            marks = [mark for mark, present in (('installed', item.get('selected')), ('latest', item.get('event_id') == latest),
                                                ('invalid', not item.get('valid', True))) if present]
            label = self.app.release_label(item) + (' · ' + ', '.join(marks) if marks else '')
            button = Button(label, id=f'version-{index}', name=str(item.get('event_id', '')), classes='version')
            button.disabled = not installed or bool(item.get('selected')) or not item.get('valid', True)
            rows.append(button)
        if page.get('pinned'):
            rows.insert(0, self.note('This game is pinned to the installed release; updates are not applied automatically.'))
        await self.replace_list('#store-versions', rows or [self.note('No release history is known yet.')])

    def choose_release(self, button):
        """Switching resets saved data, so it takes a second press to confirm."""
        if self.app.running.get(self.address):
            self.show_feedback('Stop the game before switching releases.')
            return
        if not self.confirm.confirm(button.name):
            self.show_feedback('Press again to switch to this release. Its saved data and settings will be reset.')
            return
        self.select_release(button.name)

    @work(exclusive=True, group='store-change')
    async def select_release(self, event_id):
        self.show_feedback('Switching release…')
        try:
            result = await asyncio.to_thread(self.app.runtime.select, self.address, event_id)
        except RuntimeError:
            self.show_feedback('The release could not be selected. Close the game and retry.')
            return
        self.show_feedback('Switched release. Saved data was reset.' if result.get('data_reset') else 'Release selected.')
        self.app.details.pop(self.address, None)
        self.app.load(background=True)
        self.read_details()
        self.load_tab('releases')

    async def show_snapshots(self, page):
        if not page:
            await self.replace_list('#store-snapshots', [self.note(
                'Snapshots are temporarily unavailable.' if page is False else 'No snapshots of this game are known.')])
            return
        rows = [self.note('Snapshots are frozen versions of this game. Play Now opens one without installing it.')]
        for relation, title in (('author_snapshot', 'By the creator'), ('derivative', 'By other creators')):
            items = [item for item in page.get('items', []) if item.get('relation') == relation and item.get('address')]
            if items:
                rows.append(self.note(title))
            for item in items:
                name = item.get('name') or 'Snapshot'
                rows.append(Button(f'▶ {name} · {self.app.release_label(item)}', name=item['address'], classes='snapshot',
                                   disabled=not item.get('valid', True)))
        await self.replace_list('#store-snapshots', rows if len(rows) > 1 else [self.note('No snapshots of this game are known.')])

    async def show_rules(self, data):
        self.rules = []
        if not data:
            await self.replace_list('#store-rules', [self.note(
                'Permissions are temporarily unavailable.' if data is False else 'Permissions are available for installed games.')])
            return
        rows = []
        domains = [('Required', data.get('required_domains') or []), ('Optional', data.get('optional_domains') or [])]
        for label, items in domains:
            if items:
                rows.append(self.note(f'{label} network access: ' + ', '.join(str(item) for item in items)))
        rules = data.get('saved_rules') or []
        if not rules:
            rows.append(self.note('No saved permission answers. The game asks when it needs one.'))
        for rule in rules:
            index = len(self.rules)
            permission, subject, decision = rule.get('permission', ''), rule.get('subject', ''), rule.get('decision', '')
            self.rules.append((permission, subject, decision))
            text = permission + (f' ({subject})' if subject else '') + ': ' + ('allowed' if decision == 'allow' else 'denied')
            # A new dispatch allow needs a handler target this API cannot supply.
            allow = Button('Allow', name=str(index), classes='rule', disabled=decision == 'allow' or permission == 'dispatch')
            deny = Button('Deny', name=str(index), classes='rule', disabled=decision == 'deny')
            rows.append(Horizontal(Static(text, markup=False), allow, deny,
                                   Button('Forget', name=str(index), classes='rule'), classes='rule-row'))
        await self.replace_list('#store-rules', rows)

    @work(exclusive=True, group='store-change')
    async def change_permission(self, permission, subject, action):
        runtime = self.app.runtime
        try:
            if action == 'forget':
                await asyncio.to_thread(runtime.clear_permission, self.address, permission, subject)
            else:
                await asyncio.to_thread(runtime.set_permission, self.address, permission, action, subject)
        except RuntimeError:
            self.show_feedback('The permission could not be changed. Please retry.')
            return
        self.show_feedback('Permission forgotten.' if action == 'forget' else f'Permission set to {action}.')
        self.load_tab('permissions')

    def show_feedback(self, text):
        self.notice = text
        self.update_page()

    def gamepad_l1(self):
        return self.cycle_tab(-1)

    def gamepad_r1(self):
        return self.cycle_tab(1)

    def gamepad_hints(self, focused):
        return [('A', 'Select'), ('B', 'Back'), ('L1/R1', 'Sections')]

    def action_back(self):
        self.app.pop_screen()
