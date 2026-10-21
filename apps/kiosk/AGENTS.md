# Launcher guidance

Before changing the launcher UI, read the **Launcher style and interaction
contract** in [docs/kiosk.md](../../docs/kiosk.md). This records the user's
approved direction and should guide future launcher work.

Keep the shell compact and discovery focused: larger primary navigation buttons, responsive
artwork cards, and a dedicated Napplet details/store page. Keep Library and
Discovery search state separate. Clicking or pressing Enter on a discovery
card opens details; Play and Install are explicit actions there. Preserve mouse, keyboard and
controller parity (every control reachable with the D-pad and activated with
A; add `gamepad_<button>` hooks where a screen needs more), the shared kwak theme, in-app settings, and navigation state. Use
kwakore as the game service and reuse the existing image cache and settings
controls. Avoid adding large headings, outer gutters, or decorative panels
that reduce the browsing area.

My Games also uses a full-width cover grid with a separate game page. Keep the
primary Play button (which becomes Stop while running) and Update control near
game identity and status; put Game Info and
Releases below them. Back restores the originating query, cover selection, and
scroll position. Do not reinstate a visible library list or an inline details
sidebar unless the user changes this direction.

Keep routine data refresh invisible: update on tab entry and background
intervals, preserve existing content, focus, scroll, and form edits, and retry
temporary failures automatically. Do not add Refresh, Scan, or Check updates
buttons for routine reads. Keep intentional actions such as Play, Install,
Update, Connect, and Publish explicit.
