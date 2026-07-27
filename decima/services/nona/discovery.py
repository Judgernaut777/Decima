"""Plug-in-or-forge: ranking what already exists BEFORE authoring anything new (N6).

MOST GAPS ARE NOT GAPS. The policy is the reference's, and the order is the whole point
(`heartbeat/decima/discovery.py:3-14`, design §5.8 point 1):

  1. rank the LIVE capability catalogue against the goal — if something already does this,
     the answer is a suggestion to USE it, and no source is generated;
  2. if nothing clears an integer threshold, consult an injected `research` seam (an
     external registry / index / operator list) — plug one in rather than reinvent it;
  3. only if both miss, FORGE — and forging here means "propose a candidate", which is the
     start of the quarantine → evaluation → promotion path, not a new capability.

Forging last is not politeness. A forged organ costs an evaluation, a promotion signature,
a canary and a permanent maintenance surface; a catalogue hit costs a grant. Any system
that forges first will accrete near-duplicate organs faster than anyone can audit them.

THE RANKING IS THE SHIPPING ONE. `decima/projections/search.py` already implements a
deterministic, integer-only, IDF-weighted lexical scorer with an exact-content-token
citability gate, and `decima/capabilities/qa.py` already demonstrates the discipline of
ranking, then GATING, then reporting the mode honestly. This module reuses both rather
than porting the reference's hashed bag-of-words: a second ranking implementation would be
a second thing to keep deterministic, and its scores would be incomparable with every
other relevance number in the product. Every score here is an `int`; the threshold must be
an `int` (a bool is refused too — `True` is not a threshold); ranking the same fold twice
yields byte-identical scores.

MATCHED TOKENS ARE THE EVIDENCE. A bare number tells an operator nothing about WHY an
entry ranked, so every match carries the exact content tokens it shared with the goal —
the same "show the grounding, not just the score" rule the Q&A citability gate follows.

NOTHING AUTO-ACTIVATES. This module writes NOTHING to the log and installs nothing. A
`use` result is a SUGGESTION: acting on it still means a grant (through
`powerbox.request_capability`, which routes its own decision), and forging still means
`ProposeCapability` → `EvaluateCandidate` → `PromoteCandidate` (gated). The reference's
"use" path could install a handler on approval; ours cannot, because there is nothing here
to approve. And an organ whose TIER has no bound executor is reported as NOT EXECUTABLE
rather than being offered as usable — `executor.TIER_PROFILES` is the single source of
truth for that, an absent entry is an absence of a path, and a stub handler is never
registered to paper over it (design §5.8, closing paragraph).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from decima.kernel.hashing import nfc
from decima.kernel.weave import Cell, Weave
from decima.projections.knowledge import KnowledgeItem, KnowledgeProjection
from decima.projections.search import SearchIndex, content_tokens
from decima.services.nona import candidate as candidate_mod
from decima.services.nona import executor, promotion

CAPABILITY = "capability"

# The three actions, in the order they are tried.
USE = "use"
PLUG_IN = "plug_in"
FORGE = "forge"

# Retrieval modes, reported honestly like `capabilities.qa.retrieve_with_mode` does: a
# caller must be able to tell "the catalogue answered" from "nothing cleared the bar".
LEXICAL = "lexical"
EMPTY = "empty"  # there was nothing live to rank at all

# A research seam yields DESCRIPTORS (dicts describing a tool someone else already built).
# It is injected, never defaulted: a default would be a network dependency in a local-first
# product, and an absent seam must mean "we did not look", not "we looked and invented".
Research = Callable[[str], Sequence[dict[str, Any]]]


@dataclass(frozen=True)
class Match:
    """One catalogue entry ranked against a goal. Every number is an int, and
    `matched_tokens` is the evidence for the number."""

    capability: str
    name: str
    effect: str
    tier: str | None
    score: int
    matched_tokens: tuple[str, ...]
    quarantined: bool
    executable: bool
    signer_policy: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "name": self.name,
            "effect": self.effect,
            "tier": self.tier,
            "score": int(self.score),
            "matched_tokens": list(self.matched_tokens),
            "quarantined": self.quarantined,
            "executable": self.executable,
            "signer_policy": self.signer_policy,
        }


def is_executable(tier: str | None, effect: str) -> bool:
    """Is there a BOUND way to run this, right now?

    Two independent facts, both structural: the effect must be one Nona actually handles
    (`executor.organ_effects` declares exactly `generated_code`, and the invoke seam
    refuses an effect with no handler), and the tier must map to a worker profile. A
    missing profile is not a withheld permission — it is the absence of an executor, and
    an approval prompt cannot conjure one.
    """
    if effect != executor.GENERATED_CODE:
        # A non-generated capability is executed by whatever declared its effect; Nona has
        # no opinion and does not claim one.
        return True
    return isinstance(tier, str) and tier in executor.TIER_PROFILES


def executability_note(tier: str | None, effect: str) -> str:
    """The honest sentence for an unrunnable organ — never "requires approval"."""
    if is_executable(tier, effect):
        return ""
    return (
        f"promoted, not executable — no executor is bound for tier {tier!r} "
        f"(effect {effect!r}); nothing an operator can approve makes it run"
    )


def catalogue_text(weave: Weave, cell: Cell) -> str:
    """The searchable text of one capability: its name, effect, target, tier, and — when
    it is a Nona organ — the INTENT its candidate recorded.

    The intent matters because a goal is phrased in intent language ("summarise a
    document"), not in capability-naming language (`organ:7f3a…`). Nothing untrusted is
    executed or interpolated: this text feeds a lexical index and nothing else.
    """
    content = cell.content if isinstance(cell.content, dict) else {}
    parts = [
        str(content.get("name") or ""),
        str(content.get("effect") or ""),
        str(content.get("target") or ""),
        str(content.get("declared_effect_class") or ""),
    ]
    candidate_id = content.get("candidate")
    if isinstance(candidate_id, str) and candidate_id:
        cand = weave.get(candidate_id)
        if cand is not None and cand.type == candidate_mod.CANDIDATE:
            parts.append(str(cand.content.get("intent") or ""))
    return nfc(" ".join(p for p in parts if p))


def build_index(weave: Weave) -> tuple[SearchIndex, dict[str, str]]:
    """A disposable lexical index over the LIVE capability catalogue.

    Built by folding an EMPTY `KnowledgeProjection` and adding one item per capability, so
    the shipping scorer is reused exactly (same tokenizer, same integer IDF, same gate)
    without pretending a capability is a knowledge note. Every item is stamped
    `instruction_eligible=False` / `trust="untrusted"`: a catalogue entry's text may have
    come from generated source, and it describes, never instructs.

    Returns the index plus the id → text map, because `Hit.snippet` is truncated and the
    matched-token evidence must be computed against the whole text.
    """
    index = SearchIndex(KnowledgeProjection())
    texts: dict[str, str] = {}
    for cid, cell in sorted(weave.cells.items()):
        if cell.type != CAPABILITY or cell.retracted:
            continue
        text = catalogue_text(weave, cell)
        if not text:
            continue
        texts[cid] = text
        index.add_item(
            KnowledgeItem(
                id=cid,
                type=CAPABILITY,
                text=text,
                instruction_eligible=False,
                trust="untrusted",
                provenance=tuple(cell.provenance),
            )
        )
    return index, texts


def rank(weave: Weave, goal: str, *, limit: int = 5) -> list[Match]:
    """Rank the live catalogue against `goal` — deterministic, integer-only, no writes.

    Ordering, ties and scores come from `SearchIndex.query`, which gates on exact content
    -token overlap (so a capability sharing only stopwords with the goal is never a match)
    and sorts by `(score, text, cell)`. Two runs over the same fold produce byte-identical
    output; that is a property worth a test, because a ranking that drifts turns "why did
    it pick that?" into an unanswerable question.
    """
    index, texts = build_index(weave)
    goal_tokens = content_tokens(goal)
    out: list[Match] = []
    for hit in index.query(nfc(goal), limit=max(0, int(limit))):
        cell = weave.get(hit.cell)
        if cell is None:  # pragma: no cover - the index was built from this fold
            continue
        content = cell.content if isinstance(cell.content, dict) else {}
        tier = content.get("declared_effect_class")
        tier = tier if isinstance(tier, str) else None
        effect = str(content.get("effect") or "")
        out.append(
            Match(
                capability=hit.cell,
                name=str(content.get("name") or ""),
                effect=effect,
                tier=tier,
                score=int(hit.score),
                matched_tokens=tuple(sorted(goal_tokens & content_tokens(texts[hit.cell]))),
                quarantined=bool(content.get("quarantined")),
                executable=is_executable(tier, effect),
                signer_policy=promotion.signer_policy(tier or ""),
            )
        )
    return out


def discover(
    weave: Weave,
    goal: str,
    *,
    threshold: int,
    research: Research | None = None,
    limit: int = 5,
) -> dict[str, Any]:
    """The plug-in-or-forge dispatcher. Pure: it reads the fold and writes nothing.

    `threshold` is an INT and is validated as one — a float threshold would make the
    decision unreplayable the moment it were recorded, and `True` is not a number. The
    returned dict always carries `mode`, the ranked `matches` (with their matched tokens),
    and `action`:

      * `use` — the best match cleared the threshold. It is a SUGGESTION: `grant_required`
        says so, and `executable` / `note` say whether anything could run it at all.
        Nothing is activated, nothing is installed, no Cell is written.
      * `plug_in` — the catalogue missed but the injected research seam returned
        descriptors. They are DATA (`instruction_eligible: False`) describing tools that
        exist elsewhere; adopting one is a separate, explicit act.
      * `forge` — both missed. The reply names the next step (`ProposeCapability`) rather
        than taking it, and it never contains generated source: this module has no codegen
        seam and refuses to acquire one, so an offline install cannot be surprised into
        authoring an organ (design §5.9 point 1 — refuse, never emit a stub).
    """
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        raise ValueError("threshold must be a plain int (a recorded score is never a float)")
    matches = rank(weave, goal, limit=limit)
    base: dict[str, Any] = {
        "goal": nfc(goal),
        "threshold": int(threshold),
        "mode": LEXICAL if matches else EMPTY,
        "matches": [m.as_dict() for m in matches],
    }
    best = matches[0] if matches else None
    if best is not None and best.score >= int(threshold):
        return {
            **base,
            "action": USE,
            "capability": best.capability,
            "name": best.name,
            "score": int(best.score),
            "matched_tokens": list(best.matched_tokens),
            # A suggestion, not an activation. Using it means holding a grant for it, and
            # a grant comes from the broker (which routes its own single decision) — never
            # from having been ranked highly.
            "grant_required": True,
            "quarantined": best.quarantined,
            "executable": best.executable,
            "note": executability_note(best.tier, best.effect),
        }
    descriptors = list(research(nfc(goal))) if research is not None else []
    if descriptors:
        return {
            **base,
            "action": PLUG_IN,
            "candidates": [
                {**dict(d), "instruction_eligible": False, "trust": "untrusted"}
                for d in descriptors
            ],
            "researched": True,
        }
    return {
        **base,
        "action": FORGE,
        "researched": research is not None,
        "reason": (
            "no live capability cleared the threshold"
            + ("" if research is not None else " and no research seam is bound")
        ),
        "next_step": "ProposeCapability",
    }
