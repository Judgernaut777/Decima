"""Media library + playback rail (MEDIA1) — a benign LOCAL effect run as a capability.

Playing a track is a side effect (sound leaves a speaker), but it is benign and
local: it neither moves money nor reaches outward/irreversibly into the world, so —
unlike the home/payments rails — it needs NO Morta gate. It is still NOT ambient
authority: playback runs through a forged `media.play` capability and the executor,
exactly like every other effect. ocap gates *who may play*; the registry decides only
what playing *does* (a deterministic stub — no real audio).

What this rail composes from primitives the kernel already has:

  • a `track` Cell — title, artist, and an INTEGER duration in seconds (Law: ints,
    not floats) — content-addressed by (title, artist) so re-adding is idempotent;
  • a `playlist` Cell + typed membership EDGES (`playlist —contains→ track`): playlist
    membership is graph structure on the Weft, folded onto both endpoints, not a side list;
  • a "media.play" executor effect — a DETERMINISTIC benign stub: it echoes the track
    being played and never touches an audio device. An empty/missing track id raises
    ExecError → a FAILED receipt (definite no-effect);
  • a `now_playing` STATE Cell on the Weft — a singleton updated to the played track on
    each successful play, so "what is playing right now" is folded, audited, time-travelable
    graph state like every other cell;
  • a full EffectReceipt on the Weft (status/effect_class) — every play is auditable.

Pure composition: registers its effect through the public `kernel.integrate_tool`
(→ `executor.register`) and writes cells/edges with the public `model.*` helpers — it
edits no kernel or other-module file.
"""
from decima import executor, model
from decima.hashing import content_id, nfc

PLAY_EFFECT = "media.play"          # the registered effect name
TRACK = "track"                     # Cell type — a single track (int duration)
PLAYLIST = "playlist"              # Cell type — a named playlist
NOW_PLAYING = "now_playing"        # Cell type — the singleton state cell
CONTAINS = "contains"             # edge rel: playlist —contains→ track
EffectClass = "MEDIA"             # benign local effect_class (audit signal)

# A stable id for the singleton now-playing state cell (one per kernel).
NOW_PLAYING_ID = content_id({"now_playing": "singleton"})


def _track_id(title: str, artist: str) -> str:
    return content_id({"track": nfc(str(title)), "artist": nfc(str(artist))})


def _playlist_id(name: str) -> str:
    return content_id({"playlist": nfc(str(name))})


def _play_handler(impl, args: dict) -> dict:
    """The playback rail itself — a deterministic, benign stub standing in for an
    audio backend. A real handler would hand the track to a player; here it just
    echoes what would play and returns it. A missing track id raises ExecError →
    a FAILED receipt: a definite no-effect, nothing played."""
    track = nfc(str(args.get("track", "")))
    if not track:
        raise executor.ExecError("media.play requires a track id")
    title = nfc(str(args.get("title", "")))
    return {"out": f"playing {title or track}", "track": track, "title": title}


def add_track(k, title: str, artist: str, *, secs: int) -> str:
    """Assert a `track` Cell with an INTEGER duration (seconds) on the Weft and
    return its id. Idempotent by (title, artist). Duration must be an int — Law:
    ints, not floats — a float (or non-int) is refused loudly."""
    if isinstance(secs, bool) or not isinstance(secs, int):
        raise ValueError(f"track duration must be an int (seconds), got {secs!r}")
    tid = _track_id(title, artist)
    model.assert_content(k.weft, k.root.id, tid, TRACK, {
        "title": nfc(str(title)), "artist": nfc(str(artist)), "secs": secs,
    })
    return tid


def create_playlist(k, name: str) -> str:
    """Assert a `playlist` Cell and return its id. Idempotent by name."""
    pid = _playlist_id(name)
    model.assert_content(k.weft, k.root.id, pid, PLAYLIST, {"name": nfc(str(name))})
    return pid


def add_to_playlist(k, playlist: str, track: str) -> None:
    """Add a track to a playlist as a typed membership EDGE
    (`playlist —contains→ track`) folded onto both endpoints. Idempotent: the fold
    dedups identical (rel, src, dst) edges, so re-adding is a no-op."""
    model.assert_edge(k.weft, k.root.id, playlist, CONTAINS, track)


def install_rail(k, *, name: str = PLAY_EFFECT) -> str:
    """Register the `media.play` effect and forge a capability granted to Decima.
    No Morta gate (playback is benign and local), but a sandbox profile that allows
    ONLY this effect with network DENIED (a local player needs none). Returns the
    capability id."""
    caveats = {
        "effect_class": EffectClass,
        # SB1 sandbox: only media.play may run under the cap; no network (local audio).
        "sandbox": {"effects": [name], "network": False},
    }
    return k.integrate_tool(name, _play_handler, caveats=caveats)


def track(k, title_or_id: str, artist: str | None = None):
    """The current `track` cell, by (title, artist), by id, or id-prefix; or None."""
    w = k.weave()
    c = w.get(title_or_id)
    if c is None and artist is not None:
        c = w.get(_track_id(title_or_id, artist))
    return c if (c is not None and c.type == TRACK) else None


def playlist(k, name_or_id: str):
    """The current `playlist` cell, by name or id/prefix, or None."""
    w = k.weave()
    c = w.get(name_or_id) or w.get(_playlist_id(name_or_id))
    return c if (c is not None and c.type == PLAYLIST) else None


def library(k) -> list:
    """Every (non-retracted) `track` cell in the library — the query."""
    return k.weave().of_type(TRACK)


def playlist_tracks(k, playlist_cell) -> list:
    """The `track` cells a playlist contains, by following its `contains` edges.
    `playlist_cell` may be a Cell or a playlist id."""
    w = k.weave()
    pid = getattr(playlist_cell, "id", playlist_cell)
    out = []
    for e in w.edges_from(pid, CONTAINS):
        c = w.get(e["dst"])
        if c is not None and c.type == TRACK:
            out.append(c)
    return out


def now_playing(k):
    """The singleton `now_playing` state cell (folded state), or None if nothing
    has played yet."""
    c = k.weave().get(NOW_PLAYING_ID)
    return c if (c is not None and c.type == NOW_PLAYING) else None


def play(k, agent_cell, cap_id, track_cell) -> dict:
    """Play a track via the capability/effect and, on success, UPDATE the singleton
    `now_playing` state cell on the Weft (audited). Returns
    {status, result_cell, denied?, track, title}.

    Flow: (invoke) sandboxed via the cap; the kernel emits the EffectReceipt. On
    SUCCEEDED, (re-)assert the now_playing cell pointing at the played track — a new
    CONTENT version on the Weft — so "what's playing" tracks reality and is auditable.
    A denial (sandbox/exec) leaves now_playing untouched: a definite no-effect."""
    tid = track_cell.id
    title = track_cell.content.get("title", "")
    res = k.invoke(agent_cell, cap_id, {"track": tid, "title": title})
    out = {"status": res.get("status"), "result_cell": res.get("result_cell"),
           "track": tid, "title": title}
    if "denied" in res:
        out["denied"] = res["denied"]
        return out                                       # state cell untouched
    model.assert_content(k.weft, k.root.id, NOW_PLAYING_ID, NOW_PLAYING, {
        "track": tid, "title": title, "artist": track_cell.content.get("artist", ""),
    })
    return out
