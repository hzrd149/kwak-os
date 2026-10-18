"""Pure ordering, filtering and shelf building for My Games and Find Games.

Inputs are plain dictionaries from kwakore plus the launcher's local
activity, so every rule here is testable without a running interface.
"""
from collections import Counter

SORTS = (("name", "Name"), ("recent", "Recently played"), ("updated", "Recently updated"),
         ("added", "Recently added"), ("installed", "Installed first"))
SHOWS = (("all", "All"), ("installed", "Installed"), ("favorites", "Favourites"), ("updates", "Updates"))


def label(options, key):
    return dict(options).get(key, key)


def cycle(options, key, delta=1):
    keys = [option for option, _ in options]
    return keys[(keys.index(key) + delta) % len(keys)] if key in keys else keys[0]


def show_options(collections):
    return SHOWS + tuple(("collection:" + name, name) for name in sorted(collections))


def published(game):
    version = game.get("installed_version") or game.get("version")
    created = version.get("created_at") if isinstance(version, dict) else None
    return created if isinstance(created, (int, float)) and not isinstance(created, bool) else 0


def has_update(game, installed):
    return bool(game.get("update_available") or installed.get(game["address"], {}).get("update_available"))


def name_key(game):
    return (game.get("name") or "").casefold()


def matches(game, query):
    query = query.casefold().strip()
    if not query:
        return True
    text = " ".join(str(game.get(key) or "") for key in ("name", "author_name", "description"))
    text += " " + " ".join(str(item) for item in game.get("archetypes") or ())
    return all(word in text.casefold() for word in query.split())


def library_rows(games, installed, activity, sort="name", show="all", query=""):
    """Order and filter My Games; ties always fall back to the name."""
    rows = [game for game in games if matches(game, query)]
    if show == "installed":
        rows = [game for game in rows if game["address"] in installed]
    elif show == "favorites":
        rows = [game for game in rows if activity.favorite(game["address"])]
    elif show == "updates":
        rows = [game for game in rows if has_update(game, installed)]
    elif show.startswith("collection:"):
        members = set(activity.collections.get(show.split(":", 1)[1], ()))
        rows = [game for game in rows if game["address"] in members]
    rows.sort(key=name_key)
    if sort == "recent":
        rows.sort(key=lambda game: -activity.last_played(game["address"]))
    elif sort == "updated":
        rows.sort(key=lambda game: -published(game))
    elif sort == "added":
        rows.sort(key=lambda game: -activity.first_seen(game["address"]))
    elif sort == "installed":
        rows.sort(key=lambda game: game["address"] not in installed)
    return rows


def library_shelves(games, installed, running, activity):
    """Home shelves for My Games, in Steam's order; empty shelves are omitted."""
    by_address = {game["address"]: game for game in games}
    ordered = sorted(games, key=name_key)
    shelves = [
        ("Continue playing", [game for game in ordered if game["address"] in running]),
        ("Recently played", [by_address[address] for address in activity.recent(12) if address in by_address]),
        ("Favourites", [game for game in ordered if activity.favorite(game["address"])]),
    ]
    for name in sorted(activity.collections):
        members = set(activity.collections[name])
        shelves.append((name, [game for game in ordered if game["address"] in members]))
    shelves.append(("Updates available", [game for game in ordered if has_update(game, installed)]))
    shelves.append(("Recently added", sorted(
        (game for game in ordered if activity.first_seen(game["address"])),
        key=lambda game: -activity.first_seen(game["address"]))[:12]))
    shelves.append(("All games", ordered))
    return [(title, rows) for title, rows in shelves if rows]


def compatible(game):
    return game.get("available", False) and not game.get("missing_domains")


def archetype_counts(catalog):
    return Counter(str(archetype) for game in catalog for archetype in game.get("archetypes") or ()
                   if str(archetype).strip())


def store_shelves(catalog, installed, categories=4, size=16):
    """Find Games home, from the cached catalog alone.

    kwakore has no editorial picks, so "New & updated" is plainly the newest
    publications that run here.
    """
    newest = sorted((game for game in catalog if compatible(game)), key=lambda game: -published(game))
    shelves = [
        ("New & updated", newest[:size]),
        ("Try instantly", sorted((game for game in catalog if compatible(game) and game["address"] not in installed),
                                 key=name_key)[:size]),
    ]
    for archetype, _ in archetype_counts(catalog).most_common(categories):
        shelves.append((archetype.capitalize(), sorted(
            (game for game in catalog if archetype in (game.get("archetypes") or ())),
            key=lambda game: (not compatible(game), name_key(game)))[:size]))
    return [(title, rows) for title, rows in shelves if rows]
