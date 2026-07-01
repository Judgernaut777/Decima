"""Context-fold — Law 5 applied to the LLM context window (fold, don't summarize).

A pure-library lane: it builds its OWN message history (no Kernel needed) and proves
the deterministic, zero-LLM context fold holds every invariant:

  - fold keeps the last `keep_recent` turns at FULL fidelity and skeletonizes the
    rest (kept_count == keep_recent; folded_count == the remainder);
  - DETERMINISM — two folds of the same history are byte-identical (canonical
    serialization equal, and `frozen_prefix` equal): the cache-warm anchor;
  - EXACT IDENTIFIERS survive folding — a UUID, an absolute /path/like/this, and a
    long hex id living in an OLD (paged-out) turn are still present, verbatim, in the
    fold output (the coordinate closet + inline skeleton preserved them);
  - char_after < char_before (structural compression);
  - stats are ints only;
  - extract_identifiers is deterministic + sorted + unique.

Contract: run(k, line). `k` is ignored — this is a pure library. Fail loud.
"""
import json

from decima import context_fold as CF
from decima.hashing import canonical


def run(k, line):  # noqa: ARG001 — pure library; ignore the shared kernel
    line("\n== CONTEXT-FOLD (Law 5 for the context window — fold, don't summarize) ==")

    UUID = "550e8400-e29b-41d4-a716-446655440000"
    PATH = "/var/log/decima/weft.db"
    HEX = "deadbeefcafe1234"
    URL = "https://decima.dev/api:8443/v1/status"

    # An OLD turn (index 0) carrying every exact coordinate — it MUST be paged out
    # (skeletonized) yet lose none of its identifiers.
    history = [
        {"role": "user",
         "content": (f"Deploy build {HEX} using config at {PATH}; the tenant is "
                     f"{UUID} and the health endpoint is {URL}. " + "padding " * 40)},
        {"role": "assistant", "content": "Understood, starting the deploy. " + "detail " * 40},
        {"role": "tool", "content": "step 1 log output " + "line " * 40},
        {"role": "assistant", "content": "step 1 done, continuing " + "more " * 40},
        {"role": "user", "content": "What is the latest status?"},
        {"role": "assistant", "content": "The deploy is green."},
    ]
    keep_recent = 2

    out = CF.fold(history, keep_recent=keep_recent)
    stats = out["stats"]

    # 1. keep last `keep_recent` full; skeletonize the rest. ─────────────────────────
    assert stats["kept_count"] == keep_recent, stats
    assert stats["folded_count"] == len(history) - keep_recent, stats
    assert stats["input_count"] == len(history), stats
    # The kept tail is byte-identical to the originals; the folded head is marked.
    assert out["messages"][-keep_recent:] == history[-keep_recent:], "recent turns not full"
    assert all(m.get("folded") for m in out["messages"][:-keep_recent]), "prefix not folded"
    line(f"  fold keeps last {keep_recent} turns FULL, skeletonizes the other "
         f"{stats['folded_count']} (zero LLM) ✓")

    # 2. Determinism — byte-identical output + frozen_prefix. ────────────────────────
    out2 = CF.fold(history, keep_recent=keep_recent)
    ser = canonical({k2: out[k2] for k2 in ("messages", "coordinates", "stats")})
    ser2 = canonical({k2: out2[k2] for k2 in ("messages", "coordinates", "stats")})
    assert ser == ser2, "fold is not byte-identical across calls"
    fp1 = CF.frozen_prefix(history, keep_recent=keep_recent)
    fp2 = CF.frozen_prefix(history, keep_recent=keep_recent)
    assert fp1 == fp2 and isinstance(fp1, str), "frozen_prefix not byte-identical"
    line("  determinism: two folds byte-identical; frozen_prefix stable (cache-warm) ✓")

    # 3. Exact identifiers in an OLD (folded) turn survive verbatim. ─────────────────
    blob = json.dumps({"messages": out["messages"], "coordinates": out["coordinates"]})
    for ident in (UUID, PATH, HEX):
        assert ident in out["coordinates"], f"{ident} missing from coordinate closet"
        assert ident in blob, f"{ident} lost from fold output"
        assert 0 in out["coordinates"][ident], f"{ident} not traced to folded turn 0"
    # And they are present INLINE in the skeleton body too, not only in the closet.
    skel0 = out["messages"][0]["content"]
    assert UUID in skel0 and PATH in skel0 and HEX in skel0, "ids not inline in skeleton"
    line("  exact ids (UUID + /abs/path + hex) preserved verbatim in a folded turn ✓")

    # 4. Compression. ────────────────────────────────────────────────────────────────
    assert stats["char_after"] < stats["char_before"], stats
    line(f"  compression: char_after {stats['char_after']} < char_before "
         f"{stats['char_before']} ✓")

    # 5. Stats are ints only. ────────────────────────────────────────────────────────
    assert all(isinstance(v, int) and not isinstance(v, bool) for v in stats.values()), stats
    line("  stats are ints (no floats, no wall-clock) ✓")

    # 6. extract_identifiers — deterministic + sorted + unique. ──────────────────────
    text = f"{URL} {PATH} {HEX} {UUID} again {HEX} and {PATH}"
    ids_a = CF.extract_identifiers(text)
    ids_b = CF.extract_identifiers(text)
    assert ids_a == ids_b, "extract_identifiers not deterministic"
    assert ids_a == sorted(set(ids_a)), "extract_identifiers not sorted+unique"
    assert len(ids_a) == len(set(ids_a)), "extract_identifiers has duplicates"
    for ident in (UUID, PATH, HEX, URL):
        assert ident in ids_a, f"{ident} not extracted"
    line("  extract_identifiers deterministic, sorted, unique (coordinate closet) ✓")

    # 7. budget folds MORE recent turns to fit (deterministic). ──────────────────────
    tight = CF.fold(history, keep_recent=5, budget=400)
    assert tight["stats"]["char_after"] <= 400 or tight["stats"]["kept_count"] == 0, tight["stats"]
    assert tight["stats"]["kept_count"] <= 5, tight["stats"]
    line("  budget reduces kept turns deterministically until it fits ✓")

    line("  → the context window is a FOLD of the history: bounded, deterministic, "
         "byte-identical prefix, exact coordinates never lost — Law 5 for the LLM.")
