"""Programmatic capital — ephemeral cards + pluggable fiat/crypto rails (CAPITAL1).

D3.4's "ephemeral-card-as-capability" made concrete. A purchase does not hand an
agent a payment method; it mints a **capability that IS the limit**: an ephemeral,
single-use, merchant-locked, amount-capped grant, auto-revoked after one charge.
Blast-radius reduction is the card being the cap.

The mapping (specs/CAPABILITY_MAP.md D3.4), in pure ocap terms — Decima never holds
a raw PAN or a private key:

  • The PROVIDER credential (a Stripe `rk_` restricted key / a Coinbase CDP wallet
    key) lives ONLY in the CRED1 secrets broker. Decima holds a scoped, revocable
    *handle*; the raw value never lands in a cell. (Law 1.)
  • A card is an **attenuated child** of the rail's FINANCIAL pay-capability (PAY1):
        - `budget = amount_cap`     — the hard spending_limit (authorize enforces it);
        - `target = merchant_category` — the merchant lock (allowed_categories);
        - `requires_approval = True`   — the real-time authorization webhook, i.e. the
          synchronous **Morta gate** at the instant money moves.
    Attenuation is downhill (capability.attenuate), so a card can only ever be
    *narrower* than the rail.
  • A charge is **Morta-gated** (denied until approved), **refused** if amount > cap
    or merchant ∉ category, **idempotent** (no double-charge), and on success the
    card is **REVOKED** — single-use, so any reuse fails closed.
  • Both rails — `"stripe"` (fiat) and `"coinbase"` (crypto/USDC) — sit behind ONE
    FINANCIAL contract. Fiat-vs-crypto is a routing decision (which rail cap the
    card descends from); the gateway is the kernel, not the framework.

Pure composition: it wraps payments.pay / the secrets broker / capability.attenuate /
the kernel's public grant+invoke+revoke. It reinvents no processing — it models the
wrap, and edits no core file.
"""
from decima import payments, executor
from decima.capability import attenuate, capability_content
from decima.hashing import content_id, nfc
from decima.weft import ASSERT
from decima.secrets import SecretsBroker

# The two supported rails → the provider whose credential the broker holds. Both
# resolve to the SAME FINANCIAL effect contract (payments.install_rail); the rail
# name only selects which scoped pay-cap the card descends from + which (stub)
# provider settles it.
RAILS = {
    "stripe":   {"kind": "fiat",   "service": "stripe-issuing",  "cred": "rk_…"},
    "coinbase": {"kind": "crypto", "service": "coinbase-cdp",    "cred": "cdp-wallet-key"},
}


class CapitalDesk:
    """Issues ephemeral cards over fiat + crypto rails behind one FINANCIAL contract.

    Holds, per rail, the rail's FINANCIAL pay-capability (PAY1) and a scoped CRED1
    handle to that rail's provider credential — never the raw credential. A card is
    a single-use, merchant-locked, amount-capped attenuation of the rail cap.
    """

    def __init__(self, k, broker: SecretsBroker | None = None, *, rail_cap: int = 10_000):
        self.k = k
        self.broker = broker or SecretsBroker(k)
        # Each card is signed under a desk principal (the issuer of record).
        self.principal = k.keyring.mint("capital-desk", "desk")
        self._rails: dict[str, dict] = {}     # rail -> {pay_cap, cred_handle, cfg}
        for rail, cfg in RAILS.items():
            # The rail's FINANCIAL pay-capability — the parent a card attenuates from.
            pay_cap = payments.install_rail(k, cap=int(rail_cap),
                                            name=f"capital.pay.{rail}", rail=rail)
            k.approve(pay_cap)   # the RAIL is operator-enabled; per-CHARGE approval is the card's Morta gate
            # Provider credential → broker (opaque). Decima gets a scoped handle only.
            self.broker.store(f"cred:{rail}", cfg["cred"], service=cfg["service"])
            cred_handle = self.broker.issue(f"cred:{rail}", k.weave().get(k.decima_agent_id),
                                            purpose=f"settle:{rail}")
            self._rails[rail] = {"pay_cap": pay_cap, "cred_handle": cred_handle, "cfg": cfg}

    # -- mint: an ephemeral, single-use, merchant-locked, amount-capped card ---
    def mint_card(self, k, agent_cell, *, amount_cap: int, merchant_category: str,
                  rail: str) -> dict:
        """Mint an EPHEMERAL, SINGLE-USE card: an attenuation of the rail's FINANCIAL
        pay-cap, scoped to `amount_cap` (budget) + `merchant_category` (target/merchant
        lock) + Morta. Granted to `agent_cell`. The provider cred stays in the broker;
        the returned card carries only its handle id, never the secret. Returns a card
        dict {card, rail, cap_id, cred_handle, amount_cap, merchant_category}."""
        if rail not in self._rails:
            raise ValueError(f"unknown rail {rail!r} (fiat=stripe / crypto=coinbase)")
        r = self._rails[rail]
        merchant_category = nfc(merchant_category)

        # An ephemeral CARDHOLDER: the card gets its OWN principal + agent so its
        # `budget` caveat is a PER-CARD spend cap (the kernel's running-spend ledger
        # is keyed by agent). The card IS the limit; one holder, one card, isolated
        # blast radius — not a shared pool against the requesting agent's budget.
        holder = self.k.keyring.mint(f"cardholder:{rail}:{merchant_category}", "cardholder")
        grantee = holder.id
        parent = self.k.weave().get(r["pay_cap"])
        # The granter must be the principal that HELD the parent grant — the rail's
        # FINANCIAL pay-cap is granted to Decima, so Decima (the desk acts on its
        # authority) issues the downhill card. Keeps the delegation chain granter-held.
        granter = parent.content["grantee"]

        # Downhill: budget shrinks to the cap, target locks to the category, Morta on.
        card_cap = attenuate(parent.content,
                             {"budget": int(amount_cap),
                              "target": merchant_category,        # merchant lock (allowed_categories)
                              "merchant_category": merchant_category,
                              "requires_approval": True},
                             r["pay_cap"], grantee=grantee, granter=granter)
        card_cap["target"] = merchant_category                    # the card's selector IS the merchant lock
        card_id = content_id({"card": rail, "cat": merchant_category, "cap": int(amount_cap),
                              "to": grantee, "n": self.k.weft.lamport})
        self.k.weft.append(self.principal.id, ASSERT,
                           {"cell": card_id, "type": "capability", "content": card_cap})
        # The cardholder agent — its envelope is exactly this one card. The requesting
        # agent (`agent_cell`) is recorded as the card's beneficiary lineage.
        holder_agent_id = content_id({"cardholder": card_id})
        self.k.weft.append(self.principal.id, ASSERT, {
            "cell": holder_agent_id, "type": "agent",
            "content": {"principal": holder.id, "objective": f"hold one {rail} card",
                        "envelope": [card_id], "budget": int(amount_cap),
                        "sandbox": False, "lineage": agent_cell.id},
        })
        return {"card": card_id, "rail": rail, "cap_id": card_id, "holder": holder_agent_id,
                "cred_handle": r["cred_handle"], "amount_cap": int(amount_cap),
                "merchant_category": merchant_category, "single_use": True}

    # -- charge: Morta-gated, scoped, idempotent, single-use (auto-revoke) -----
    def charge(self, k, agent_cell, card: dict, *, amount: int, merchant: str,
               merchant_category: str | None = None, idempotency_key: str) -> dict:
        """Run a charge on the card. Morta-gated (denied until the card cap is
        approved); REFUSED if amount > cap or merchant ∉ category; idempotent (a
        replayed key returns the prior receipt, no double-charge). After a SUCCEEDED
        single-use charge the card is REVOKED → reuse fails closed.

        Composes payments.pay on the card cap (which descends from the rail's FINANCIAL
        pay-cap), signed by the card's own ephemeral cardholder principal so the budget
        is a per-card cap. Returns the pay-result, plus {refused?, revoked?, rail}."""
        cap_id = card["cap_id"]
        cat = card["merchant_category"]
        holder_cell = self.k.weave().get(card["holder"])          # the card signs its own charge
        # Namespace the idempotency key to the card so two cards never collide on the
        # shared (rail-level) payment dedupe index.
        key = f"{cap_id[:12]}:{nfc(str(idempotency_key))}"

        # (refuse) merchant must fall under the card's locked category. The caller
        # states the merchant's category; default to the merchant string itself so a
        # mismatch is refused rather than silently allowed.
        mcat = nfc(merchant_category if merchant_category is not None else merchant)
        if mcat != cat:
            return {"refused": f"merchant {merchant!r} (category {mcat!r}) outside card "
                               f"lock {cat!r}", "rail": card["rail"], "revoked": False}
        # (refuse) over-cap: the card's budget is the hard limit. authorize also
        # enforces this, but refuse early with a clear reason (no INVOKE written).
        if int(amount) > int(card["amount_cap"]):
            return {"refused": f"amount {amount} over card cap {card['amount_cap']}",
                    "rail": card["rail"], "revoked": False}

        # (Morta + spend-cap + idempotent + receipt) — compose the FINANCIAL rail,
        # signed by the card's own cardholder (so `budget` is a per-card cap).
        res = payments.pay(k, holder_cell, cap_id, amount=int(amount),
                           payee=merchant, idempotency_key=key)
        res["rail"] = card["rail"]
        res["revoked"] = False
        if "denied" in res:                       # Morta not yet satisfied / authz refusal
            return res

        # (single-use) a SUCCEEDED, non-replayed charge spends the card → revoke it.
        if res.get("status") == executor.SUCCEEDED and not res.get("idempotent_replay"):
            self.k.revoke(cap_id)                 # RETRACT → any reuse fails closed
            res["revoked"] = True
        return res

    def approve(self, card: dict) -> None:
        """The synchronous Morta gate: approve THIS card's per-charge authorization
        (the real-time `approve:true`). Without it, charge() is denied."""
        self.k.approve(card["cap_id"])
