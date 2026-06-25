"""CRM1: a sales pipeline by composition over Cells + CONTACTS1. Proves deals are
first-class Cells (int value, edged to contacts), advance LWW through canonical
stages (an unknown stage is REFUSED, fail closed), and pipeline() groups by stage
with correct INT totals — all on the Weft. Contract: run(k, line). Fail loud."""
from decima import crm, contacts


def run(k, line):
    line("\n== CRM1 (deals as Cells · contacts edged · LWW stages · int pipeline) ==")

    # Contacts via the CONTACTS1 PUBLIC API (we compose it; we do not edit it).
    alice = contacts.add_contact(k, "Alice Rivera", handles={"email": "alice@coop.io"})
    bob = contacts.add_contact(k, "Bob Stone", handles={"email": "bob@acme.example"})

    # Add deals — int value (minor units), one linked to a contact at creation.
    d1 = crm.add_lead(k, "Acme renewal", value=120_000, contact=alice)
    d2 = crm.add_lead(k, "Globex pilot", value=45_000)
    d3 = crm.add_lead(k, "Initech upsell", value=30_000)
    assert d1 and d2 and d3, "deals were not created"
    cell = k.weave().get(d1)
    assert cell.type == crm.DEAL and cell.content["stage"] == crm.NEW, cell.content
    assert cell.content["value"] == 120_000 and isinstance(cell.content["value"], int), \
        "deal value must be an int in minor units"
    line(f"  added: Acme {d1[:8]}={cell.content['value']} · Globex {d2[:8]} · Initech {d3[:8]} (ints, stage=new)")

    # The contact link is an EDGE folded onto both endpoints (deal —about_contact→ contact).
    assert alice in crm.contact_of(k, d1), "about_contact edge missing on deal"
    assert any(e["rel"] == crm.ABOUT_CONTACT and e["src"] == d1
               for e in k.weave().get(alice).edges_in), "edge not folded onto contact.edges_in"
    # link_contact after the fact also works.
    crm.link_contact(k, d2, bob)
    assert bob in crm.contact_of(k, d2), "link_contact did not add the edge"
    line(f"  linked: Acme→Alice {alice[:8]} (folded both ends) · Globex→Bob {bob[:8]}")

    # Advance through stages — LWW: the latest CONTENT version wins, history kept.
    crm.advance(k, d1, crm.QUALIFIED)
    crm.advance(k, d1, crm.PROPOSAL)
    crm.advance(k, d1, crm.WON)          # several advances; final stage is the live one
    crm.advance(k, d2, crm.QUALIFIED)
    crm.advance(k, d3, crm.LOST)
    won = k.weave().get(d1)
    assert won.content["stage"] == crm.WON, f"LWW: latest stage should win, got {won.content['stage']}"
    assert won.version >= 3, f"each advance should re-version the deal cell, version={won.version}"
    line(f"  advanced (LWW): Acme→won (v{won.version}) · Globex→qualified · Initech→lost")

    # FAIL CLOSED: an unknown stage is refused and writes NOTHING.
    before = k.weave().get(d2).content["stage"]
    refused = False
    try:
        crm.advance(k, d2, "negotiating")     # not a canonical stage
    except ValueError:
        refused = True
    assert refused, "advance to an unknown stage must fail closed"
    assert k.weave().get(d2).content["stage"] == before, \
        "a refused advance must not change the deal's stage"
    line(f"  fail-closed: advance(Globex, 'negotiating') refused; stage stayed '{before}'")

    # pipeline(): deals grouped by stage with correct INT totals.
    pipe = crm.pipeline(k)
    assert d1 in pipe[crm.WON]["deals"] and pipe[crm.WON]["value"] == 120_000, pipe[crm.WON]
    assert d2 in pipe[crm.QUALIFIED]["deals"] and pipe[crm.QUALIFIED]["value"] == 45_000, pipe[crm.QUALIFIED]
    assert d3 in pipe[crm.LOST]["deals"] and pipe[crm.LOST]["value"] == 30_000, pipe[crm.LOST]
    assert pipe[crm.NEW]["count"] == 0, "no deal should remain in 'new'"
    total = sum(g["value"] for g in pipe.values())
    assert total == 195_000 and all(isinstance(g["value"], int) for g in pipe.values()), \
        f"pipeline totals must be int and reconcile, got {total}"
    line(f"  pipeline: won={pipe[crm.WON]['value']} qualified={pipe[crm.QUALIFIED]['value']} "
         f"lost={pipe[crm.LOST]['value']} · Σ={total} (ints) ✓")

    line("  → deals are Cells (int value) edged to CONTACTS1 contacts; advance is LWW "
         "and fails closed on an unknown stage; pipeline is a read-only int fold on the Weft.")
