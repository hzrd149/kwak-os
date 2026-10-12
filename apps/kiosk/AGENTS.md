# Launcher guidance

Before changing the launcher UI, read the **Launcher style and interaction
contract** in [docs/kiosk.md](../../docs/kiosk.md). This records the user's
approved direction and should guide future launcher work.

Keep the shell compact and discovery focused: a thin navigation bar, responsive
artwork cards, and clear selected-game controls. Preserve mouse and keyboard
parity, the shared kwak theme, in-app settings, and navigation state. Use
kwakore as the game service and reuse the existing image cache and settings
controls. Avoid adding large headings, outer gutters, or decorative panels
that reduce the browsing area.

Keep routine data refresh invisible: update on tab entry and background
intervals, preserve existing content, focus, scroll, and form edits, and retry
temporary failures automatically. Do not add Refresh, Scan, or Check updates
buttons for routine reads. Keep intentional actions such as Play, Install,
Update, Connect, and Publish explicit.
