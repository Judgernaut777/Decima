"""TRAVEL1 — trips & bookings by COMPOSITION over the payments rail (D-cycle 16).

A trip is an itinerary of scheduled segments (a flight, a hotel, …) and a booking
is money leaving the box. So the only authority this module touches is the one the
kernel already hardened for that: a booking moves money *through* the PAY1 rail —
Morta-gated (denied until approved), spend-capped (over-cap refused), idempotent (a
replayed idempotency key never double-books). TRAVEL1 forges no capability and edits
no rail; it asserts its own analytic Cells (`trip`, `segment`, `booking`) and links
the booking to the payment's signed EffectReceipt so the money's provenance stays on
the Weft.

What it adds:
  - `create_trip(k, name, *, dest)` — assert a `trip` Cell (a name, a destination).
  - `add_segment(k, trip, kind, at, *, detail)` — assert an itinerary `segment` Cell
    (kind=flight/hotel/…, an INT logical time `at`), edged `of_trip → trip`.
  - `rail(k, *, pay_cap)` — install/forge (idempotently) the FINANCIAL rail this trip
    books through, at a hard spend cap of `pay_cap`; returns the capability id so a human
    can `k.approve` it (the Morta gate). `book` installs the SAME rail, so the caller
    approves once and every booking on the cap is then gated by that one decision.
  - `book(k, agent, trip, segment, *, amount, idempotency_key, pay_cap)` — run a
    Morta-gated payment via `payments.pay` over that rail, record a `booking` Cell and a
    `receipt` edge to the payment's EffectReceipt. DENIED before approval (Morta) and
    REFUSED over the cap — either books NOTHING. A duplicate idempotency key returns the
    prior booking, no double-spend. The rail is forged but NEVER auto-approved here: only
    a human/policy clears the Morta gate.
  - `itinerary(k, trip)` — the trip's segments in `at` order (a pure fold projection).

LAWS honored: a booking moves money ONLY through the Morta-gated, spend-capped,
idempotent payments rail (composed, never re-implemented); amounts are INTS in minor
units (no float money); no ambient authority (asserts only its own Cells via the
public `model` API; the only forged authority is the rail's, via `payments.install_rail`);
provenance on the Weft (every booking carries a `receipt` edge to its signed receipt,
and segments an `of_trip` edge to the trip).
"""
from decima import model, payments, executor
from decima.hashing import content_id, nfc

TRIP = "trip"
SEGMENT = "segment"
BOOKING = "booking"


def _rail_name(pay_cap: int) -> str:
    # One rail per cap value, named deterministically: install_rail is content-addressed
    # by name, so booking twice at the same cap re-forges the SAME capability (one cap
    # id, one approval, one running spend total) rather than a fresh unbounded one.
    return f"travel-pay-{int(pay_cap)}"


def rail(k, *, pay_cap: int) -> str:
    """Install/forge (idempotently) the FINANCIAL payments rail bookings flow through,
    at a hard spend cap of `pay_cap`, and return its capability id. The rail is forged
    DENIED (Morta `requires_approval`) — a human/policy must `k.approve(cap_id)` before
    any booking on it can settle. Re-calling at the same cap returns the same cap id."""
    if not isinstance(pay_cap, int) or isinstance(pay_cap, bool):
        raise TypeError(f"pay_cap must be int minor units, got {pay_cap!r}")
    return payments.install_rail(k, cap=int(pay_cap), name=_rail_name(pay_cap))


def create_trip(k, name: str, *, dest: str, author: str = None) -> str:
    """Assert a `trip` Cell (a name + a destination) and return its id. Content-addressed
    by (name, dest) so re-declaring the same trip is an idempotent overwrite, not a new one."""
    author = author or k.decima_agent_id
    name, dest = nfc(name), nfc(dest)
    cid = content_id({"trip": name, "dest": dest})
    model.assert_content(k.weft, author, cid, TRIP, {"name": name, "dest": dest})
    return cid


def add_segment(k, trip: str, kind: str, at: int, *, detail: str = "",
                author: str = None) -> str:
    """Assert an itinerary `segment` Cell and return its id, edged `of_trip → trip`.

    `kind` is the segment kind (flight / hotel / …). `at` is an INT logical tick (the
    point in the itinerary it sits at) — a float/bool `at` is a hard error so no float
    ever reaches signed content. The `of_trip` edge keeps the segment→trip link on the
    Weft, so `itinerary` is a pure fold, not an out-of-band index."""
    if not isinstance(at, int) or isinstance(at, bool):
        raise TypeError(f"segment at must be an int logical tick, got {type(at).__name__}")
    cell = k.weave().get(trip)
    if cell is None or cell.type != TRIP:
        raise ValueError(f"no trip {trip!r}")
    author = author or k.decima_agent_id
    kind, detail = nfc(kind), nfc(detail)
    # Salt with the Weft head so two same-kind segments at the same `at` are distinct
    # events (two hotels at tick 0 are two segments, not one idempotent overwrite).
    cid = content_id({"segment": kind, "trip": trip, "at": int(at), "salt": k.weft.head})
    model.assert_content(k.weft, author, cid, SEGMENT, {
        "trip": trip, "kind": kind, "at": int(at), "detail": detail, "booked": False,
    })
    model.assert_edge(k.weft, author, cid, "of_trip", trip)
    return cid


def itinerary(k, trip: str) -> list:
    """The trip's `segment` Cells in (at, id) order — a pure projection over the current
    fold (the segments whose `of_trip` edge points at this trip)."""
    out = [c for c in k.weave().of_type(SEGMENT) if c.content.get("trip") == trip]
    out.sort(key=lambda c: (int(c.content["at"]), c.id))
    return out


def _booking_id(trip: str, segment: str, idempotency_key: str) -> str:
    """Content-address a booking by its segment + idempotency key — a replay of the same
    key for the same segment lands on the same booking Cell (no double-book)."""
    return content_id({"booking": segment, "trip": trip,
                       "idempotency_key": nfc(str(idempotency_key))})


def book(k, agent, trip: str, segment: str, *, amount: int, idempotency_key: str,
         pay_cap: int, payee: str = "travel-rail", author: str = None) -> dict:
    """Book a segment by moving money through the PAY1 rail — Morta-gated, spend-capped,
    idempotent — and record a `booking` Cell linked to the payment's EffectReceipt.

    Returns {status, booking, receipt_cell, denied?, idempotent_replay, amount, paid}.

    Flow:
      - install/forge (idempotently) the FINANCIAL rail at `pay_cap` — but NEVER approve
        it here: the Morta gate is a human's/policy's call (`rail()` + `k.approve`), so a
        booking before approval is DENIED, exactly as it should be;
      - `payments.pay` runs the charge: DENIED before approval and REFUSED over the cap
        (then this books NOTHING — `paid` False, no `booking` Cell, money never moved);
        a duplicate idempotency key returns the prior receipt with no second spend;
      - on a SUCCEEDED charge, assert a `booking` Cell (segment, amount, receipt id) and
        a `receipt` edge to the signed EffectReceipt — the money's provenance on the Weft.

    `amount` is INT minor units (no float money). A duplicate booking call (same key)
    returns the existing booking and does not double-book."""
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise TypeError(f"booking amount must be int minor units, got {amount!r}")
    seg = k.weave().get(segment)
    if seg is None or seg.type != SEGMENT:
        raise ValueError(f"no segment {segment!r}")
    if seg.content.get("trip") != trip:
        raise ValueError(f"segment {segment!r} is not part of trip {trip!r}")
    author = author or k.decima_agent_id
    key = nfc(str(idempotency_key))
    bid = _booking_id(trip, segment, key)

    # An already-recorded booking for this (segment, key) — return it, don't re-book.
    existing = k.weave().get(bid)
    if existing is not None and existing.type == BOOKING:
        return {"status": existing.content["status"], "booking": bid,
                "receipt_cell": existing.content.get("receipt_cell"),
                "amount": existing.content.get("amount"), "paid": True,
                "idempotent_replay": True}

    # Compose the PAY1 rail: forge (idempotently) the FINANCIAL cap — NOT approved here;
    # the Morta gate stays a human's call. Then run the spend-capped, idempotent pay.
    cap_id = rail(k, pay_cap=int(pay_cap))
    pay = payments.pay(k, agent, cap_id, amount=int(amount), payee=payee,
                       idempotency_key=key)

    out = {"status": pay.get("status"), "amount": int(amount),
           "idempotent_replay": bool(pay.get("idempotent_replay")), "paid": False,
           "booking": None, "receipt_cell": pay.get("result_cell")}
    if "denied" in pay:
        # Refused by the rail (pre-approval / over-cap): book NOTHING, money never moved.
        out["denied"] = pay["denied"]
        return out
    if pay.get("status") != executor.SUCCEEDED:
        return out

    # Charge succeeded — record the booking and link it to the signed receipt.
    receipt_cell = pay["result_cell"]
    model.assert_content(k.weft, author, bid, BOOKING, {
        "trip": trip, "segment": segment, "amount": int(amount),
        "idempotency_key": key, "status": pay["status"], "receipt_cell": receipt_cell,
    })
    model.assert_edge(k.weft, author, bid, "receipt", receipt_cell)
    model.assert_edge(k.weft, author, bid, "books", segment)
    # Mark the segment booked (LWW re-assert on the same Cell id).
    booked = dict(seg.content)
    booked["booked"] = True
    model.assert_content(k.weft, author, segment, SEGMENT, booked)

    out["paid"] = True
    out["booking"] = bid
    out["receipt_cell"] = receipt_cell
    return out
