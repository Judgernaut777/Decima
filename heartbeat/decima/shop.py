"""Shop / orders on the payments rail — "place an order" by composition, not reinvention.

`CAPABILITY_MAP` D3.4 (commerce/orders). Placing an order moves money, so it is the
canonical irreversible effect with a catalog line attached, and it composes the
primitives already built rather than minting a new authority:

  - **money movement = `payments.pay`** (PAY1): FINANCIAL effect_class, a hard spend
    cap, Morta `requires_approval`, and an **idempotency key** so a replayed order
    never double-charges (no double-order);
  - **a catalog `product` Cell** holds the SKU → {name, price} (price is an INT in
    minor units), asserted on the Weft;
  - **an `order` Cell** records agent/sku/qty/amount/status on the Weft, linked to its
    payment receipt (`paid_by`) and the product (`for_product`) — provenance on the Weft.
    An order is `placed` only on a real charge; over-cap is `refused`; pre-approval is
    `denied`.

Pure composition: it calls `payments`/`kernel`/`model` PUBLIC APIs and edits none of
them, no core file. A real storefront/fulfilment engine slots in behind the rail stub
the same way a real payment provider does.
"""
from decima import payments, executor, model
from decima.hashing import content_id, nfc

PRODUCT = "product"
ORDER = "order"


# ── catalog (a product is a Weft Cell keyed by SKU) ─────────────────────────
def _product_id(sku: str) -> str:
    return content_id({"product": nfc(sku)})


def add_item(k, sku: str, name: str, price: int) -> str:
    """Add (or LWW re-assert) a catalog `product` Cell for `sku` at `price` (an INT
    in minor units). Returns the product Cell id."""
    sku = nfc(sku)
    pid = _product_id(sku)
    model.assert_content(k.weft, k.decima_agent_id, pid, PRODUCT, {
        "sku": sku, "name": nfc(str(name)), "price": int(price),
    })
    return pid


def product(weave, sku: str):
    """The catalog `product` Cell for `sku`, or None."""
    return weave.get(_product_id(sku))


# ── the order path ──────────────────────────────────────────────────────────
def order(k, agent_cell, sku: str, qty: int, *, idempotency_key: str, pay_cap: str,
          account: str = "default", prediction=None, confidence: int = 900_000) -> dict:
    """Place an order for `qty` of `sku` on the payments rail `pay_cap`: pay the
    principal (qty·price) (Morta-gated, spend-capped, idempotent), and record an
    `order` Cell on the Weft.

    Returns {order, status, placed, denied?, amount, receipt, payment, idempotent_replay}.
    A pre-approval order is `denied`; an over-cap order is `refused`; a duplicate (same
    idempotency_key) returns the prior receipt and does NOT double-charge — the order
    stays `placed` with the same receipt link."""
    sku, key = nfc(sku), nfc(str(idempotency_key))
    prod = product(k.weave(), sku)
    if prod is None:
        raise executor.ExecError(f"unknown sku {sku!r}")
    price = int(prod.content["price"])
    qty = int(qty)
    amount = price * qty

    # The order IS a Morta-gated, spend-capped, idempotent payment (PAY1). A duplicate
    # key returns the prior receipt: no second charge, no double-order.
    pr = payments.pay(k, agent_cell, pay_cap, amount=amount, payee=f"shop:{sku}",
                      idempotency_key=key, prediction=prediction, confidence=confidence)

    placed = (pr.get("status") == executor.SUCCEEDED and "denied" not in pr)
    if "denied" in pr:
        status = "refused" if "budget" in (pr["denied"] or "").lower() else "denied"
    else:
        status = "placed" if placed else (pr.get("status") or "failed")

    oid = content_id({"order": sku, "qty": qty, "key": key})
    model.assert_content(k.weft, k.decima_agent_id, oid, ORDER, {
        "sku": sku, "qty": qty, "price": price, "amount": amount,
        "account": nfc(account), "idempotency_key": key, "status": status,
        "placed": placed, "receipt": pr.get("result_cell"), "wager": pr.get("wager"),
    })
    model.assert_edge(k.weft, k.decima_agent_id, oid, "for_product", _product_id(sku))
    if pr.get("result_cell"):                       # provenance: order → receipt on the Weft
        model.assert_edge(k.weft, k.decima_agent_id, oid, "paid_by", pr["result_cell"])

    return {"order": oid, "sku": sku, "qty": qty, "price": price, "amount": amount,
            "status": status, "placed": placed, "denied": pr.get("denied"),
            "receipt": pr.get("result_cell"), "payment": pr, "wager": pr.get("wager"),
            "idempotent_replay": bool(pr.get("idempotent_replay"))}


def orders(k, account: str | None = None) -> list:
    """Order history — every `order` Cell on the Weft (optionally filtered by account),
    newest-folded last."""
    out = [c for c in k.weave().of_type(ORDER)]
    if account is not None:
        out = [c for c in out if c.content.get("account") == nfc(account)]
    return out
