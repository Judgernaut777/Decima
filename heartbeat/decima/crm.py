"""CRM1 — a sales pipeline by COMPOSITION over Cells, edges, and CONTACTS1.

`CAPABILITY_MAP` Part B (CRM / sales). A deal is a first-class Cell (Law 3): a
`lead`/`deal` we are working through a pipeline. Like every other Cell it carries
provenance on the Weft (author + parents) — no side table, no foreign keys. A deal
edged to a person reuses CONTACTS1's PUBLIC API: the link is an EDGE
`deal —about_contact→ contact`, folded onto both endpoints, so the relationship
lives on the log exactly like `contact —knows→ contact`.

What it adds is purely deal state + a read-only projection:
  - `add_lead(k, name, *, contact=None)` — assert a `deal` Cell at stage "new" with
    an int `value` (minor units), optionally edged to a CONTACTS1 contact. Content-
    addressed by NAME so re-adding the same deal keeps one identity (LWW history).
  - `advance(k, deal, stage)` — re-version the deal at a new pipeline stage (LWW:
    the latest CONTENT assertion wins). An unknown stage FAILS CLOSED (ValueError) —
    nothing is written, so the deal never lands in an undefined stage.
  - `pipeline(k)` — group live deals by stage with a total `value` per stage. A pure
    read-only fold over `weave().of_type("deal")`; every total is an INT.
  - `link_contact(k, deal, contact)` — assert the `about_contact` edge after the fact.

LAWS honored: deal `value` is an INT in minor units (no float ever enters a number
we report or sum); no ambient authority (we read the Weave and assert only our own
`deal` Cells / edges via the public `model` helpers — no `invoke`, no money moves);
provenance lives on the Weft; a deal edged to externally-sourced contact PII inherits
CONTACTS1's PII-is-DATA law (the contact stays DATA, never an instruction).
"""
from __future__ import annotations

from decima import contacts
from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

DEAL = "deal"

# The pipeline stages, in order. A stage outside this set is refused (fail closed).
NEW = "new"
QUALIFIED = "qualified"
PROPOSAL = "proposal"
WON = "won"
LOST = "lost"
STAGES = (NEW, QUALIFIED, PROPOSAL, WON, LOST)

# A deal —about_contact→ contact edge (folded onto both endpoints, like CONTACTS1).
ABOUT_CONTACT = "about_contact"


def deal_id(name: str) -> str:
    """Content-address a deal by NAME (nfc) so re-adding the same deal is idempotent
    and a deal keeps one identity across stage changes (LWW re-versions the cell)."""
    return content_id({"deal": nfc(name)})


def add_lead(k, name: str, *, value: int = 0, contact: str | None = None) -> str:
    """Add a `deal` Cell at stage "new", returning its id.

    `value` is an INT in minor units (the deal's worth); floats are refused so no
    rounding ever enters a number we later sum. `contact`, if given, is a CONTACTS1
    contact id — we assert an `about_contact` EDGE to it (composing the contacts
    public API; we do not edit it)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("deal value must be an int (minor units), not a float")
    name = nfc(name)
    cid = deal_id(name)
    assert_content(k.weft, k.human.id, cid, DEAL, {
        "name": name,
        "stage": NEW,
        "value": int(value),
        # text mirror so a deal is surfaceable the way other Cells are.
        "text": name,
    })
    if contact is not None:
        link_contact(k, cid, contact)
    return cid


def advance(k, deal: str, stage: str) -> str:
    """Move `deal` to `stage` (one of STAGES). LWW: this asserts a fresh CONTENT
    version of the deal cell, so the latest stage wins and history is auditable.

    FAIL CLOSED: an unknown stage raises ValueError and writes NOTHING — the deal can
    never be advanced into an undefined stage."""
    stage = nfc(stage)
    if stage not in STAGES:
        raise ValueError(f"unknown pipeline stage {stage!r}; must be one of {STAGES}")
    cell = k.weave().get(deal)
    if cell is None or cell.type != DEAL:
        raise ValueError(f"not a deal cell: {deal!r}")
    content = dict(cell.content)
    content["stage"] = stage
    assert_content(k.weft, k.human.id, deal, DEAL, content)
    return deal


def link_contact(k, deal: str, contact: str) -> None:
    """Assert a `deal —about_contact→ contact` EDGE on the Weft (folded onto both
    endpoints). `contact` is a CONTACTS1 contact id — composition, not coupling."""
    assert_edge(k.weft, k.human.id, deal, ABOUT_CONTACT, contact)


def contact_of(k, deal: str) -> list[str]:
    """The contact ids a deal is edged to (its `about_contact` dsts)."""
    return [e["dst"] for e in k.weave().edges_from(deal, ABOUT_CONTACT)]


def pipeline(k) -> dict:
    """Group live (non-retracted) deals by stage. Returns a dict keyed by every stage
    (in canonical order) → {"deals": [ids], "count": int, "value": int}, where `value`
    is the INT sum of those deals' values. A read-only fold over the Weave."""
    out = {s: {"deals": [], "count": 0, "value": 0} for s in STAGES}
    for c in k.weave().of_type(DEAL):
        if c.retracted:
            continue
        stage = c.content.get("stage", NEW)
        if stage not in out:                 # a fold should never see one, but be safe
            out[stage] = {"deals": [], "count": 0, "value": 0}
        val = c.content.get("value", 0)
        val = int(val) if isinstance(val, int) and not isinstance(val, bool) else 0
        out[stage]["deals"].append(c.id)
        out[stage]["count"] += 1
        out[stage]["value"] += val
    return out
