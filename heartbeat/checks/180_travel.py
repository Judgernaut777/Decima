"""TRAVEL1 — trips & bookings by composition over the PAY1 payments rail.

Proves `decima.travel` forges no new authority: a trip is an itinerary of `at`-ordered
segments, and a booking moves money ONLY through the Morta-gated, spend-capped,
idempotent rail. It shows: an itinerary projects in `at` order; a booking is DENIED
before the human approves the FINANCIAL cap, then BOOKED after (with the signed
EffectReceipt linked on the Weft); a duplicate idempotency key does NOT double-book;
an over-cap booking is REFUSED (money never moves, no booking recorded).

Runs on its OWN fresh Kernel (it forges a FINANCIAL cap and moves "money"; keep it out
of the shared kernel's state). Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima import travel, payments, executor
from decima.kernel import Kernel


def run(_k, line):
    line("\n== TRAVEL1 (trip · ordered itinerary · Morta-gated booking · idempotent · over-cap refused) ==")
    k = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)   # isolated
    decima = lambda: k.weave().get(k.decima_agent_id)
    spent = lambda: k.spent.get(k.decima_agent_id, 0.0)

    # ---- (1) a trip + segments; itinerary is in `at` order -------------------
    trip = travel.create_trip(k, "Kyoto spring", dest="Kyoto")
    tc = k.weave().get(trip)
    assert tc is not None and tc.type == "trip" and tc.content["dest"] == "Kyoto", tc
    # Add out of order on purpose — the projection must sort by `at`.
    s_hotel = travel.add_segment(k, trip, "hotel", 20, detail="ryokan")
    s_flight = travel.add_segment(k, trip, "flight", 10, detail="SFO→KIX")
    s_train = travel.add_segment(k, trip, "train", 30, detail="to Osaka")
    itin = travel.itinerary(k, trip)
    ats = [c.content["at"] for c in itin]
    kinds = [c.content["kind"] for c in itin]
    assert ats == [10, 20, 30], ats                       # ordered by logical time
    assert kinds == ["flight", "hotel", "train"], kinds
    # every segment is edged to its trip (provenance on the Weft)
    assert all(any(e["dst"] == trip for e in k.weave().edges_from(c.id, "of_trip"))
               for c in itin), "segments edge → trip"
    line(f"  trip {trip[:8]} (Kyoto): 3 segments added out of order → itinerary "
         f"{kinds} at {ats} ✓")

    # ---- (2) Morta: a booking is DENIED until the FINANCIAL cap is approved ---
    PAY_CAP = 50_000      # minor units (hard spend cap)
    cap_id = travel.rail(k, pay_cap=PAY_CAP)              # forge the rail (DENIED until approved)
    d0 = travel.book(k, decima(), trip, s_flight, amount=40_000,
                     idempotency_key="flight-1", pay_cap=PAY_CAP)
    assert "denied" in d0 and "approval" in d0["denied"].lower(), d0
    assert d0["paid"] is False and d0["booking"] is None, d0
    assert spent() == 0.0, spent()                        # money never moved
    assert not k.weave().of_type("booking"), "nothing booked pre-approval"
    line(f"  pre-approval: book(flight, 40000) DENIED — {d0['denied']}")

    # ---- approve the cap (a human/policy clears the Morta gate), then book ----
    k.approve(cap_id)
    line("  (a human approves the FINANCIAL capability — Morta gate)")
    b1 = travel.book(k, decima(), trip, s_flight, amount=40_000,
                     idempotency_key="flight-1", pay_cap=PAY_CAP)
    assert b1["paid"] and b1["status"] == executor.SUCCEEDED and not b1.get("denied"), b1
    assert spent() == 40_000.0, spent()
    bc = k.weave().get(b1["booking"])
    assert bc is not None and bc.type == "booking", bc
    assert isinstance(bc.content["amount"], int) and bc.content["amount"] == 40_000
    # the booking links the signed EffectReceipt on the Weft (money's provenance)
    receipt = k.weave().get(b1["receipt_cell"])
    assert receipt.content["effect_class"] == payments.FINANCIAL
    assert receipt.content["status"] == executor.SUCCEEDED
    assert any(e["dst"] == b1["receipt_cell"]
               for e in k.weave().edges_from(b1["booking"], "receipt")), "receipt edge on Weft"
    # the segment is now marked booked
    assert k.weave().get(s_flight).content["booked"] is True
    line(f"  approved: book(flight, 40000) → booking {b1['booking'][:8]} → receipt "
         f"{b1['receipt_cell'][:8]} (class={receipt.content['effect_class']}, "
         f"spent={int(spent())}/{PAY_CAP}) ✓")

    # ---- (3) a duplicate (same idempotency key) does NOT double-book ---------
    dup = travel.book(k, decima(), trip, s_flight, amount=40_000,
                      idempotency_key="flight-1", pay_cap=PAY_CAP)
    assert dup["idempotent_replay"] and dup["booking"] == b1["booking"], dup
    assert spent() == 40_000.0, spent()                   # unchanged — no second charge
    bookings = k.weave().of_type("booking")
    assert len(bookings) == 1, len(bookings)              # one booking, not two
    fin = [c for c in k.weave().of_type("result")
           if c.content.get("effect_class") == payments.FINANCIAL]
    assert len(fin) == 1, len(fin)                        # one charge on the Weft
    line(f"  duplicate flight-1 → idempotent replay (same booking, spent still "
         f"{int(spent())}); bookings={len(bookings)}, FINANCIAL receipts={len(fin)} ✓")

    # ---- (4) an over-cap booking is REFUSED (running cap; money never moves) --
    over = travel.book(k, decima(), trip, s_hotel, amount=40_000,
                       idempotency_key="hotel-1", pay_cap=PAY_CAP)   # 40000+40000 > 50000
    assert "denied" in over and "budget" in over["denied"].lower(), over
    assert over["paid"] is False and over["booking"] is None, over
    assert spent() == 40_000.0, spent()                   # still just the flight
    assert len(k.weave().of_type("booking")) == 1, "over-cap booked nothing"
    assert k.weave().get(s_hotel).content["booked"] is False
    line(f"  over-cap: book(hotel, 40000) with 40000 already spent (cap {PAY_CAP}) "
         f"REFUSED — {over['denied']} ✓")

    line("  → TRAVEL1 composes the rail: itineraries order by `at`, a booking is "
         "Morta-gated + spend-capped + idempotent, receipts linked on the Weft.")
