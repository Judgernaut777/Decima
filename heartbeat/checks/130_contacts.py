"""CONTACTS1: people as first-class Cells — relationships on the Weft, and the
PII-is-DATA law (externally-sourced contact info is never instruction-eligible)."""
from decima import contacts


def run(k, line):
    line("\n== CONTACTS (people as Cells · edges on the Weft · PII is DATA) ==")

    # Add two contacts I vetted myself → trusted, instruction-eligible facts.
    alice = contacts.add_contact(
        k, "Alice Rivera",
        handles={"email": "alice@coop.io", "x": "@arivera"},
        notes="Met at the archive co-op; runs the budget.")
    bob = contacts.add_contact(
        k, "Bob Stone", handles={"email": "bob@acme.example"},
        notes="Engineer at Acme.")
    acme = contacts.add_org(k, "Acme")
    assert alice and bob and acme, "contacts/org were not created"
    line(f"  added: Alice {alice[:8]} · Bob {bob[:8]} · org Acme {acme[:8]}")

    # A relationship is an EDGE on the Weft, folded onto both endpoints.
    contacts.link(k, alice, contacts.KNOWS, bob)
    contacts.link(k, bob, contacts.WORKS_AT, acme)
    w = k.weave()
    assert bob in contacts.related(k, alice, contacts.KNOWS), "knows edge missing"
    assert acme in contacts.related(k, bob, contacts.WORKS_AT), "works_at edge missing"
    # the edge really landed on both endpoints (src.edges_out / dst.edges_in)
    assert any(e["rel"] == contacts.KNOWS and e["src"] == alice
               for e in w.get(bob).edges_in), "edge not folded onto dst.edges_in"
    line(f"  edges: Alice —knows→ Bob ; Bob —works_at→ Acme (folded both ends)")

    # Reuse the model/memory entity convention: relate a contact to an entity.
    ent = contacts.relate_entity(k, alice, "Alice Rivera")
    assert ent in contacts.related(k, alice, contacts.ABOUT), "about-entity edge missing"
    assert k.weave().get(ent).type == "entity", "entity cell wrong type"
    line(f"  entity: Alice —about→ entity {ent[:8]} (same cell memory points at)")

    # find by name and by an attribute (a handle).
    by_name = contacts.find(k, "Rivera")
    assert [c.id for c in by_name] == [alice], [c.id for c in by_name]
    by_handle = contacts.find(k, "@arivera")
    assert alice in [c.id for c in by_handle], "find-by-handle missed Alice"
    by_note = contacts.find(k, "Acme")  # Bob's note mentions Acme
    assert bob in [c.id for c in by_note], "find-by-note missed Bob"
    line(f"  find: 'Rivera'→{len(by_name)} · '@arivera'→{len(by_handle)} · 'Acme'→{len(by_note)}")

    # THE LAW: an externally-sourced contact (scraped PII) is DATA, never an
    # instruction — instruction_eligible=False even though it is recallable.
    scraped = contacts.add_contact(
        k, "Carol External",
        handles={"email": "carol@unknown.example"},
        notes="Ingested from a third-party directory.", trusted=False)
    cell = k.weave().get(scraped)
    assert cell.content["instruction_eligible"] is False, \
        "externally-sourced contact must NOT be instruction-eligible (PII is DATA)"
    assert cell.content["recallable"] is True, "scraped contact should still be recallable as data"
    # and a trusted self-added contact IS instruction-eligible (contrast)
    assert k.weave().get(alice).content["instruction_eligible"] is True, \
        "trusted contact should be instruction-eligible"
    found_scraped = [c.id for c in contacts.find(k, "Carol")]
    assert scraped in found_scraped, "scraped contact should be findable as data"
    line(f"  PII law: scraped {scraped[:8]} instruction_eligible=False, recallable=True "
         f"(DATA, never instruction); trusted Alice eligible=True")
