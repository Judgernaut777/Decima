"""Contacts / people — first-class Cells over the types-as-data model.

A contact is a Cell (Law 3): a person/org we hold handles and notes for. Like a
memory claim, a contact carries the four-permission boundary — most relevantly
`instruction_eligible`. PII / contact data ingested from *outside* (a scraped
vCard, an email signature, a third-party directory) is DATA: it is written
`instruction_eligible=False` so the brain may recall and act on it as facts but
never as instructions (the same recall-vs-instruct law memory and the browser
receipt obey). A contact you add yourself (or vet) is `trusted=True`.

Relationships are EDGEs on the Weft, not foreign keys in a side table:
`contact —knows→ contact`, `contact —works_at→ org`, `contact —about→ entity`
(reusing the model/memory entity convention). Each edge is folded onto both
endpoints (src.edges_out / dst.edges_in), so provenance lives on the log.

`find` is a thin substring scan over name / handles / notes, honoring the same
`recallable` gate memory uses. A real semantic index swaps in behind it later;
no new dependency is pulled into the Heartbeat.
"""
from __future__ import annotations

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

CONTACT = "contact"
ORG = "org"

# Relationship rels are normalized (nfc) on the edge; named here for callers.
KNOWS = "knows"
WORKS_AT = "works_at"
ABOUT = "about"          # contact —about→ entity (reuse model entity convention)


def contact_id(name: str) -> str:
    """Content-address a contact by NAME (nfc) so re-adding the same person is
    idempotent and a contact keeps one identity across versions."""
    return content_id({"contact": nfc(name)})


def org_id(name: str) -> str:
    return content_id({"org": nfc(name)})


def entity_id(name: str) -> str:
    """Same content address `memory.entity_id` uses, so a contact's `about`
    entity is the very cell memory claims also point at."""
    return content_id({"entity": nfc(name)})


def _permissions(instruction_eligible: bool, recallable: bool, citable: bool) -> dict:
    return {
        "recallable": bool(recallable),
        "citable": bool(citable),
        "instruction_eligible": bool(instruction_eligible),
    }


def add_contact(k, name: str, *, handles=None, notes=None, trusted: bool = True,
                recallable: bool = True, citable: bool = True) -> str:
    """Add (or re-version) a `contact` Cell, returning its id.

    `handles` is a dict of channel→value (e.g. {"email": "a@x.io", "x": "@a"});
    `notes` a free-form string. Provenance is on the Weft (author + parents).

    THE LAW: `trusted` gates `instruction_eligible`. Externally-sourced PII must
    be added with `trusted=False` — it lands `instruction_eligible=False`, so it
    is readable/citable as DATA but can never be treated as an instruction.
    """
    name = nfc(name)
    cid = contact_id(name)
    content = {
        "name": name,
        "handles": {nfc(ch): nfc(str(v)) for ch, v in (handles or {}).items()},
        "notes": nfc(notes) if notes else "",
        # text mirror so find()/recall surface something meaningful
        "text": name,
        "trusted": bool(trusted),
        **_permissions(instruction_eligible=bool(trusted),
                       recallable=recallable, citable=citable),
    }
    assert_content(k.weft, k.human.id, cid, CONTACT, content)
    return cid


def add_org(k, name: str, *, notes=None, trusted: bool = True) -> str:
    """Orgs are first-class Cells too — a `works_at` edge needs a real endpoint."""
    name = nfc(name)
    cid = org_id(name)
    assert_content(k.weft, k.human.id, cid, ORG, {
        "name": name, "notes": nfc(notes) if notes else "", "text": name,
        "trusted": bool(trusted),
        **_permissions(instruction_eligible=bool(trusted), recallable=True, citable=True),
    })
    return cid


def link(k, src: str, rel: str, dst: str) -> None:
    """Assert a relationship EDGE `src —rel→ dst` on the Weft (folded onto both
    endpoints). E.g. link(k, alice, KNOWS, bob); link(k, alice, WORKS_AT, acme)."""
    assert_edge(k.weft, k.human.id, src, rel, dst)


def relate_entity(k, contact: str, entity_name: str) -> str:
    """Relate a contact to a model/memory `entity` (reuse the convention): assert
    the entity Cell if needed and an `about` edge contact —about→ entity. Returns
    the entity id, which is the SAME cell memory claims point at."""
    eid = entity_id(entity_name)
    assert_content(k.weft, k.human.id, eid, "entity", {"name": nfc(entity_name)})
    link(k, contact, ABOUT, eid)
    return eid


def _haystacks(cell) -> list[str]:
    c = cell.content
    parts = [c.get("name") or "", c.get("notes") or "", c.get("text") or ""]
    parts.extend(str(v) for v in (c.get("handles") or {}).values())
    parts.extend(str(ch) for ch in (c.get("handles") or {}).keys())
    return parts


def find(k, query: str) -> list:
    """Return contact Cells matching `query` (case-insensitive substring) across
    name / handles / notes. Honors the `recallable` permission, so a contact
    marked unrecallable is not surfaced. Results are DATA for the caller to read."""
    q = nfc(query).lower()
    out = []
    for c in k.weave().of_type(CONTACT):
        if c.retracted or not c.content.get("recallable", True):
            continue
        if any(q in h.lower() for h in _haystacks(c)):
            out.append(c)
    return out


def related(k, contact: str, rel: str | None = None) -> list[str]:
    """The dst ids of `contact`'s outgoing relationship edges (optionally one rel)."""
    return [e["dst"] for e in k.weave().edges_from(contact, nfc(rel) if rel else None)]
