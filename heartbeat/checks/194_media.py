"""MEDIA1 — a media library + playback rail: tracks (int durations) and playlist
membership as EDGES on the Weft; playback runs as a benign LOCAL effect through a
capability (no Morta gate, but no ambient authority either), updating a `now_playing`
state cell on the Weft; library + playlist queries return the right sets.

Runs on its OWN fresh Kernel (it registers an effect and forges a capability), so it
stays out of the shared kernel's state. Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import media, executor, audit
from decima.kernel import Kernel


def run(_k, line):
    line("\n== MEDIA / PLAYBACK (benign local effect · capability · state on the Weft) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    cap_id = media.install_rail(k)
    decima = lambda: k.weave().get(k.decima_agent_id)

    # ---- add tracks; durations are INTEGER seconds on the Weft -------------
    t1 = media.add_track(k, "Teardrop", "Massive Attack", secs=330)
    t2 = media.add_track(k, "Angel", "Massive Attack", secs=379)
    t3 = media.add_track(k, "Strobe", "deadmau5", secs=634)
    for tid in (t1, t2, t3):
        c = k.weave().get(tid)
        assert c is not None and c.type == "track", tid
        assert isinstance(c.content["secs"], int) and not isinstance(c.content["secs"], bool), \
            f"duration must be an int: {c.content!r}"
    assert media.add_track(k, "Teardrop", "Massive Attack", secs=330) == t1, "add_track idempotent"
    try:
        media.add_track(k, "Bad", "x", secs=3.5)               # float duration → refused
        raise AssertionError("float duration must be refused")
    except ValueError:
        pass
    line(f"  added 3 tracks (int secs: 330/379/634); float duration refused; idempotent ✓")

    # ---- build a playlist: membership is typed EDGES on the Weft -----------
    pl = media.create_playlist(k, "Trip Hop")
    media.add_to_playlist(k, pl, t1)
    media.add_to_playlist(k, pl, t2)
    media.add_to_playlist(k, pl, t2)                           # dup → folded to one edge
    plc = media.playlist(k, "Trip Hop")
    assert plc is not None and plc.id == pl and plc.type == "playlist"
    on_pl = {c.id for c in media.playlist_tracks(k, plc)}
    assert on_pl == {t1, t2}, on_pl                            # exactly the two added (deduped)
    assert t3 not in on_pl, "Strobe is not on the playlist"
    line(f"  playlist 'Trip Hop' contains {len(on_pl)} tracks via edges (dup deduped) ✓")

    # ---- library + playlist queries return the right SETS ------------------
    lib = {c.id for c in media.library(k)}
    assert lib == {t1, t2, t3}, lib                            # the whole library
    assert media.now_playing(k) is None, "nothing playing before first play"
    line(f"  library() = {len(lib)} tracks; playlist_tracks() = {len(on_pl)} ✓")

    # ---- play a track via the capability → now_playing on the Weft ---------
    teardrop = k.weave().get(t1)
    r = media.play(k, decima(), cap_id, teardrop)
    assert r["status"] == executor.SUCCEEDED and not r.get("denied"), r
    receipt = k.weave().get(r["result_cell"])
    assert receipt.content["effect_class"] == media.EffectClass
    assert receipt.content["status"] == executor.SUCCEEDED
    np = media.now_playing(k)
    assert np is not None and np.type == "now_playing" and np.content["track"] == t1, np
    assert np.content["title"] == "Teardrop", np.content
    line(f"  play 'Teardrop' → receipt {r['result_cell'][:8]} "
         f"(class={receipt.content['effect_class']}); now_playing → Teardrop ✓")

    # ---- play a second track → now_playing UPDATES (state tracks reality) ---
    r2 = media.play(k, decima(), cap_id, k.weave().get(t3))
    assert r2["status"] == executor.SUCCEEDED, r2
    assert media.now_playing(k).content["track"] == t3, "now_playing must follow the latest play"
    line(f"  play 'Strobe' → now_playing updates Teardrop→Strobe ✓")

    # the now_playing cell's history is on the signed Weft (audited)
    trail = audit.audit_trail(k, media.NOW_PLAYING_ID)
    assert trail["verifiable"] and trail["count"] >= 2, trail   # two plays = two state versions
    line(f"  audit: {trail['count']} verified events touch now_playing "
         f"(verifiable={trail['verifiable']}) ✓")
