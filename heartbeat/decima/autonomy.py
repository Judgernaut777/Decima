"""D5 — the autonomy ladder: per-(agent, capability) autonomy levels.

An agent's autonomy is **not** a global on/off switch — it is a per-capability *rung*,
and (the framework's key insight that maps cleanly onto Decima) *different steps of one
workflow run at different rungs based on reversibility/stakes*. That is already exactly
Decima's per-`effect_class` Morta gating; this module names it and makes the rung explicit
and first-class.

The five rungs (D5 of CAPABILITY_MAP.md), and the verdict each yields per `effect_class`:

  1 Read-only        — observe/analyze only. Any write/effect → REFUSE.
  2 Draft & suggest  — DISP1-style proposal + a wager; nothing executes → PROPOSE.
  3 Supervised+gates — execute REVERSIBLE; pause for approval on IRREVERSIBLE/FINANCIAL.
  4 Monitored        — act end-to-end, every action logged + notified → EXECUTE (audited).
  5 Full autonomy    — act within caveats, periodic review → EXECUTE (within scope).

Two laws (D5 / Morta):
  • **Promotion is EARNED** — a rung is raised only when a measurable track record clears a
    threshold (WV1 `calibration` — the agent's recorded success rate). Demotion is **INSTANT**
    (one call, no evidence required — the Morta "demote now" reflex).
  • **The decision + its reason are recorded on the Weft** — every `set/decide/promote/demote/
    pin` writes a Cell, so an autonomy verdict is auditable, never an ambient toggle.

A manual **pin** is honored above evidence: an owner who pins a rung holds it there until the
pin is cleared (PATTERN1's manual override), and the pin itself is recorded.

This is the QUERY / decision layer (like B4 `governance_check` and the WV1 gate). Wiring
`decide()` into every `invoke` so the kernel auto-consults the ladder before it delegates is a
later **core** step — this lane provides the rung records and the verdict.

Public `wager`/`memory`/`capability`(read)/`disposition` + `model`/`weave` API only — no core
edit. Levels and rates are ints (WEFT §4/§7: never a float in signed content).
"""
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc
from decima import wager as wv

# Cell types written by this layer.
AUTONOMY = "autonomy_level"          # the per-(agent, capability) rung Cell
AUTONOMY_DECISION = "autonomy_decision"   # a recorded decide() verdict

# The five rungs.
RUNG_READ_ONLY = 1
RUNG_PROPOSE = 2
RUNG_SUPERVISED = 3
RUNG_MONITORED = 4
RUNG_FULL = 5
RUNGS = (RUNG_READ_ONLY, RUNG_PROPOSE, RUNG_SUPERVISED, RUNG_MONITORED, RUNG_FULL)

# Verdicts decide() can return.
REFUSE = "refuse"
PROPOSE = "propose"
EXECUTE = "execute"
REQUIRE_APPROVAL = "require_approval"

# effect_class taxonomy (canonical strings used across audit/payments/kernel).
# READ/PURE are non-effecting; the rest are writes/effects of rising stakes.
READ = "READ"
PURE = "PURE"
REVERSIBLE = "REVERSIBLE"
IRREVERSIBLE = "IRREVERSIBLE"
FINANCIAL = "FINANCIAL"
_NON_EFFECTING = frozenset({READ, PURE})
# At rung 3 these stakes pause for approval; a REVERSIBLE effect runs.
_GATED_AT_SUPERVISED = frozenset({IRREVERSIBLE, FINANCIAL})

# Promotion threshold: a rung is raised only when the agent's recorded track record
# (WV1 calibration hit-rate, an int in millionths) clears this floor — and there must be
# at least a minimum sample so a single lucky bet can't promote. EARNED, not asserted.
PROMOTE_HIT_RATE = 800_000     # 80% of resolved wagers must have hit
PROMOTE_MIN_SAMPLE = 3         # …over at least this many resolved wagers


def _level_id(agent: str, capability: str) -> str:
    # Stable id per (agent, capability) so a re-set is a new VERSION (LWW), not a new Cell.
    return content_id({"autonomy_level": agent, "capability": capability})


def _clamp(level: int) -> int:
    return max(RUNG_READ_ONLY, min(RUNG_FULL, int(level)))


def set_autonomy(k, agent, capability, level, *, pinned=False, reason="set", author=None):
    """Record a per-(agent, capability) `autonomy_level` rung Cell (1 read-only … 5 full).

    A fresh set replaces the prior rung (LWW on a stable id) and records WHY on the Weft.
    `pinned` marks a manual owner pin that promotion/demotion-by-evidence must honor.
    Returns the level cell id."""
    author = author or k.decima_agent_id
    lvl = _clamp(level)            # bound to a real rung (1..5); fail-safe on a stray value
    lid = _level_id(agent, capability)
    assert_content(k.weft, author, lid, AUTONOMY, {
        "agent": agent, "capability": capability, "level": lvl,
        "pinned": bool(pinned), "reason": nfc(reason),
    })
    # Edge the rung to the capability it governs (provenance / Capability-Inspector fold).
    assert_edge(k.weft, author, lid, "autonomy_of", capability)
    return lid


def get_level(k, agent, capability):
    """The current rung Cell for (agent, capability), or None if never set."""
    return k.weave().get(_level_id(agent, capability))


def level_of(k, agent, capability, default=RUNG_READ_ONLY) -> int:
    """The current rung int. Defaults to **read-only** — the safe floor: an agent with no
    recorded autonomy for a capability may only observe (fail-safe, like UNKNOWN→closed)."""
    cell = get_level(k, agent, capability)
    return int(cell.content["level"]) if cell is not None else default


def is_pinned(k, agent, capability) -> bool:
    cell = get_level(k, agent, capability)
    return bool(cell.content.get("pinned")) if cell is not None else False


def _verdict_for(level: int, effect_class: str) -> tuple[str, str]:
    """The gate verdict for a rung at a given effect_class — the heart of D5. Pure; the
    decision is recorded by decide()."""
    ec = (effect_class or READ).upper()
    non_effecting = ec in _NON_EFFECTING

    # A read/analyze action is always allowed to execute, at EVERY rung — reading is the
    # one thing even rung-1 may do.
    if non_effecting:
        return EXECUTE, f"rung {level}: {ec} is non-effecting — observe/analyze always allowed"

    if level <= RUNG_READ_ONLY:
        return REFUSE, f"rung 1 (read-only): refuses any write/effect ({ec})"
    if level == RUNG_PROPOSE:
        return PROPOSE, (f"rung 2 (draft & suggest): propose {ec} for human approval — "
                         "nothing executes")
    if level == RUNG_SUPERVISED:
        if ec in _GATED_AT_SUPERVISED:
            return REQUIRE_APPROVAL, (f"rung 3 (supervised+gates): {ec} is irreversible/"
                                      "high-stakes — pauses for approval")
        return EXECUTE, f"rung 3 (supervised+gates): {ec} is reversible — executes"
    if level == RUNG_MONITORED:
        return EXECUTE, f"rung 4 (monitored): {ec} executes end-to-end — logged + notified"
    return EXECUTE, f"rung 5 (full autonomy): {ec} executes within caveats — periodic review"


def decide(k, agent, capability, *, effect_class, record=True, author=None) -> dict:
    """Return the autonomy gate verdict for (agent, capability) at this `effect_class`.

    Verdict ∈ {refuse, propose, execute, require_approval} with a human-legible reason. The
    decision + its reason are recorded on the Weft (a `decided_with` edge to the rung Cell) so
    the verdict is auditable — set `record=False` for a pure dry-run query. Returns
    {agent, capability, level, effect_class, verdict, reason, decision?}.

    NOTE (core wiring): auto-consulting decide() inside the kernel's `invoke` — so the ladder
    gates every delegated effect, not just an explicit caller — is a later core step. This lane
    provides the query + verdict, exactly as B4 `governance_check` does for governance."""
    author = author or k.decima_agent_id
    level = level_of(k, agent, capability)
    ec = (effect_class or READ).upper()
    verdict, reason = _verdict_for(level, ec)
    out = {"agent": agent, "capability": capability, "level": level,
           "effect_class": ec, "verdict": verdict, "reason": reason}
    if record:
        did = content_id({"autonomy_decision": agent, "capability": capability,
                          "effect_class": ec, "verdict": verdict, "at": k.weft.head})
        assert_content(k.weft, author, did, AUTONOMY_DECISION, {
            "agent": agent, "capability": capability, "level": level,
            "effect_class": ec, "verdict": verdict, "reason": reason,
        })
        lid = _level_id(agent, capability)
        if get_level(k, agent, capability) is not None:
            assert_edge(k.weft, author, did, "decided_with", lid)
        out["decision"] = did
    return out


def track_record(k) -> dict:
    """The agent's recorded track record = WV1 calibration over resolved wagers: overall
    hit-rate (int millionths) + sample size. This is the *evidence* promotion is earned by —
    the same signal that refines the router's confidence (D4.3 / D5 law (b))."""
    cal = wv.calibration(k)
    return {"hit_rate": cal["hit_rate"], "resolved": cal["resolved"], "calibration": cal}


def promotable(k, agent, capability) -> tuple[bool, str]:
    """Whether the track record clears the promotion threshold — and whether there's room to
    climb. Promotion is EARNED: hit-rate ≥ PROMOTE_HIT_RATE over ≥ PROMOTE_MIN_SAMPLE resolved
    wagers. A manual pin blocks evidence-driven promotion (the owner holds the rung)."""
    if is_pinned(k, agent, capability):
        return False, "rung is manually pinned — evidence cannot move a pinned rung"
    if level_of(k, agent, capability) >= RUNG_FULL:
        return False, "already at rung 5 (full autonomy) — no higher rung"
    tr = track_record(k)
    rate, n = tr["hit_rate"], tr["resolved"]
    if n < PROMOTE_MIN_SAMPLE:
        return False, (f"insufficient track record ({n}/{PROMOTE_MIN_SAMPLE} resolved wagers) "
                       "— promotion is earned, not asserted")
    if rate is None or rate < PROMOTE_HIT_RATE:
        shown = 0 if rate is None else rate
        return False, (f"track record below threshold ({shown/wv.FULL:.0%} < "
                       f"{PROMOTE_HIT_RATE/wv.FULL:.0%}) over {n} wagers")
    return True, (f"track record {rate/wv.FULL:.0%} over {n} wagers ≥ "
                  f"{PROMOTE_HIT_RATE/wv.FULL:.0%} — promotion earned")


def promote(k, agent, capability, *, author=None) -> dict:
    """Raise the rung by one — but ONLY when the track-record threshold is met (and not before).
    Returns {promoted, from, to?, reason}. A refused promotion changes nothing on the ladder."""
    author = author or k.decima_agent_id
    cur = level_of(k, agent, capability)
    ok, reason = promotable(k, agent, capability)
    if not ok:
        return {"promoted": False, "from": cur, "reason": reason}
    new = _clamp(cur + 1)
    set_autonomy(k, agent, capability, new, reason=f"promoted: {reason}", author=author)
    return {"promoted": True, "from": cur, "to": new, "reason": reason}


def demote(k, agent, capability, *, to=None, reason="demoted (Morta)", author=None) -> dict:
    """Demotion is **INSTANT** — no track record required, no waiting. Drops the rung (default
    to read-only, the safe floor; or to a named lower rung) immediately. This is the Morta
    reflex: trust is earned slowly and revoked at once. A demotion overrides a pin (safety wins)
    and clears it so the rung is no longer held high. Returns {demoted, from, to, reason}."""
    author = author or k.decima_agent_id
    cur = level_of(k, agent, capability)
    target = RUNG_READ_ONLY if to is None else _clamp(to)
    target = min(target, cur)            # demotion only ever lowers (never silently raises)
    # Clear any pin — a demotion is a hard reset that the pin must not block.
    set_autonomy(k, agent, capability, target, pinned=False, reason=nfc(reason), author=author)
    return {"demoted": True, "from": cur, "to": target, "reason": nfc(reason)}


def pin(k, agent, capability, level, *, reason="manual pin (owner override)", author=None) -> dict:
    """Honor a manual owner pin: fix the rung at `level` and mark it pinned, so evidence-driven
    promotion cannot move it (PATTERN1's manual override). The pin is recorded on the Weft.
    Returns {pinned, level, reason}."""
    lvl = _clamp(level)
    set_autonomy(k, agent, capability, lvl, pinned=True, reason=nfc(reason), author=author)
    return {"pinned": True, "level": lvl, "reason": nfc(reason)}


# An alias for the D5 wording ("the user can pin/override a rung manually").
override = pin


def unpin(k, agent, capability, *, author=None) -> dict:
    """Release a manual pin without changing the rung, so evidence may move it again."""
    author = author or k.decima_agent_id
    cur = level_of(k, agent, capability)
    set_autonomy(k, agent, capability, cur, pinned=False, reason="pin released", author=author)
    return {"unpinned": True, "level": cur}
