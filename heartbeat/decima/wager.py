"""WV1 — the Wager/Verdict loop: the scientific method as Cells.

Receipts record *what happened*; Decima has had no first-class record of what it
**predicted** vs. what it **got**. Before a significant action, Decima records a **wager**
— a probabilistic prediction + confidence; after, a **verdict** measures the actual outcome;
the hit/miss folds into a **calibration** signal that refines future confidence. This is the
loop that lets judgment compound — it complements Nona (which learns which *capabilities*
work) by learning which *decisions* work, and pairs with D3 (a trade or ad spend is a wager,
verified against metrics).

A **significant** wager marks an action that should clear a **Morta** approval gate before it
proceeds (a big bet needs sign-off). The gate itself is the kernel's `requires_approval` /
`approve` path; `wager.py` records the prediction and binds it to the gated action.

Public `model`/`weave`/`kernel` API only — no core edit. Confidence and rates are ints in
**millionths** (WEFT §4/§7: never a float in signed content).
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id

WAGER = "wager"
VERDICT = "verdict"
FULL = 1_000_000        # confidence/rate scale (millionths), matching memory.FULL_CONFIDENCE


def wager(k, action, prediction, confidence, significant=False, action_cap=None, author=None):
    """Record a prediction BEFORE acting. `prediction` is the predicted outcome — an int
    (compared numerically, within a tolerance, at verdict time) or a str/bool (compared by
    equality). `confidence` is an int in millionths. A `significant` wager names an action that
    should clear a Morta gate before it proceeds; `action_cap` (the capability the action uses)
    binds the prediction to that gated effect via a `wagers_on` edge. Returns the wager id."""
    author = author or k.decima_agent_id
    wid = content_id({"wager": action, "prediction": prediction, "at": k.weft.head})
    assert_content(k.weft, author, wid, WAGER, {
        "action": action, "prediction": prediction, "confidence": int(confidence),
        "significant": bool(significant), "action_cap": action_cap, "status": "open",
    })
    if action_cap:
        assert_edge(k.weft, author, wid, "wagers_on", action_cap)
    return wid


def verdict(k, wager_id, observed, tolerance=0, author=None):
    """Resolve a wager: compare prediction to the observed outcome, record a `verdict` Cell
    (hit/miss + delta) with a `verdict_of` edge to the wager, and mark the wager resolved.
    Returns {verdict, hit, delta}."""
    author = author or k.decima_agent_id
    w = k.weave().get(wager_id)
    if w is None or w.type != WAGER:
        raise ValueError(f"not a wager: {wager_id!r}")
    predicted = w.content["prediction"]
    # bool is an int subclass in Python — test it first so True/1 don't conflate.
    if isinstance(predicted, bool) or isinstance(observed, bool):
        hit, delta = (observed == predicted), None
    elif isinstance(predicted, int) and isinstance(observed, int):
        delta = observed - predicted
        hit = abs(delta) <= int(tolerance)
    else:
        hit, delta = (observed == predicted), None
    vid = content_id({"verdict_of": wager_id, "observed": observed, "at": k.weft.head})
    assert_content(k.weft, author, vid, VERDICT, {
        "wager": wager_id, "predicted": predicted, "observed": observed,
        "hit": hit, "delta": delta, "confidence": w.content["confidence"],
    })
    assert_edge(k.weft, author, vid, "verdict_of", wager_id)
    resolved = dict(w.content)
    resolved["status"], resolved["hit"] = "resolved", hit
    assert_content(k.weft, author, wager_id, WAGER, resolved)   # new version (LWW)
    return {"verdict": vid, "hit": hit, "delta": delta}


def _band(confidence: int) -> str:
    if confidence < 400_000:
        return "low"
    if confidence < 800_000:
        return "med"
    return "high"


def calibration(k) -> dict:
    """Aggregate resolved wagers into a calibration report: overall hit-rate and per-confidence-
    band hit-rate — i.e. do high-confidence predictions actually hit more often? Rates are ints
    in millionths (None for an empty band). This is the learned signal that refines future
    confidence; a well-calibrated agent's high band hits more than its low band."""
    w = k.weave()
    bands = {"low": [], "med": [], "high": []}
    hits = total = 0
    for v in w.of_type(VERDICT):
        h = 1 if v.content["hit"] else 0
        hits += h
        total += 1
        bands[_band(v.content["confidence"])].append(h)
    rate = (hits * FULL // total) if total else None
    per_band = {b: {"n": len(xs), "hit_rate": (sum(xs) * FULL // len(xs) if xs else None)}
                for b, xs in bands.items()}
    return {"resolved": total, "hits": hits, "hit_rate": rate, "bands": per_band}
