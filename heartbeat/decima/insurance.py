"""INSURANCE1 — an insurance capability by COMPOSITION (Cycle 21).

Insurance is just three existing primitives wired together: a recurring PREMIUM is
SUBS1 spend; a CLAIM is an analytic record bounded by its policy's coverage; a
PAYOUT is money LEAVING the box — the most irreversible thing Decima does — so it
runs through PAY1's Morta-gated, spend-capped FINANCIAL rail and is then posted to
the LEDGER1 double-entry ledger, citing the claim. This module forges NO new
authority and moves no money of its own: it asserts only its own `policy`/`claim`
Cells via the PUBLIC `model` API and composes `accounts`, `payments`,
`subscriptions`, and `model`.

LAWS honored:
  • ALL amounts are INTS in minor units — premium, coverage, claim amount, payout.
    A non-int (or bool) is a hard error so no float ever reaches signed content.
  • FAIL-CLOSED coverage — `file_claim` REFUSES (writes nothing) when the claim
    amount exceeds the policy's coverage. An over-coverage claim never reaches the Log.
  • A PAYOUT is Morta-gated — it goes through `payments.pay` against an approval-gated
    FINANCIAL capability; denied until a human/policy approves, capped by `pay_cap`.
  • PROVENANCE on the Weft — the payout posts a BALANCED ledger entry whose `records`
    edge cites the claim (and the payout's signed FINANCIAL receipt as its source), and
    a `pays` edge ties the claim → its receipt. Every reported number traces back.
  • NO AMBIENT AUTHORITY — a payout names the acting agent + the capability explicitly;
    a claim that exceeds coverage cannot create a payout at all.
"""
from decima import model, payments, accounts, subscriptions
from decima.hashing import content_id, nfc

POLICY = "policy"
CLAIM = "claim"
COVERS = "covers"          # claim → covers → policy (a claim is filed against a policy)
PAYS = "pays"              # claim → pays → receipt (the payout that settled it)

OPEN = "open"
PAID = "paid"


def _policy_id(name: str) -> str:
    """Content-address a policy by its NAME — re-adding the same policy (changed
    premium/coverage) lands LWW on the same Cell, history on the Log."""
    return content_id({"policy": nfc(name)})


def _int(label: str, value: int) -> int:
    """Coerce-check an int minor-unit amount (reject bool/float) so no float ever
    reaches signed content."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{label} must be an int (minor units), got {value!r}")
    if value < 0:
        raise ValueError(f"{label} must be non-negative, got {value}")
    return int(value)


# ── policies (a `policy` Cell; optionally a recurring premium via SUBS1) ─────
def add_policy(k, name: str, *, premium: int, coverage: int, category: str = None,
               every: int = None, next_at: int = None) -> str:
    """Open a `policy` Cell (int `premium`, int `coverage`) and return its id.

    `premium` and `coverage` are int minor units. If `every` and `next_at` are given,
    the recurring premium is ALSO registered as a SUBS1 subscription (the renewal clock
    is `scheduling`'s explicit-tick reminder) and a `billed_via` edge ties the policy to
    that subscription — so premium provenance lives on the Weft. Content-addressed by
    NAME (idempotent)."""
    name = nfc(name)
    premium = _int("premium", premium)
    coverage = _int("coverage", coverage)
    category = nfc(category) if category is not None else "insurance"

    pid = _policy_id(name)
    content = {"name": name, "premium": premium, "coverage": coverage,
               "category": category}

    if every is not None and next_at is not None:               # SUBS1 recurring premium
        sub = subscriptions.add_subscription(
            k, f"premium:{name}", premium, every, category=category, next_at=next_at)
        content["premium_subscription"] = sub
        model.assert_content(k.weft, k.decima_agent_id, pid, POLICY, content)
        model.assert_edge(k.weft, k.decima_agent_id, pid, "billed_via", sub)
    else:
        model.assert_content(k.weft, k.decima_agent_id, pid, POLICY, content)
    return pid


def policies(k) -> list:
    """The `policy` Cells on the Weft, in (name, id) order."""
    out = list(k.weave().of_type(POLICY))
    out.sort(key=lambda c: (c.content.get("name", ""), c.id))
    return out


def coverage_of(k, policy) -> int:
    """The int coverage of `policy` (a cell id or Cell)."""
    cell = k.weave().get(policy) if isinstance(policy, str) else policy
    if cell is None or cell.type != POLICY:
        raise ValueError(f"unknown policy {policy!r}")
    return int(cell.content["coverage"])


# ── claims (fail-closed on coverage) ────────────────────────────────────────
def file_claim(k, policy, amount: int, *, description: str) -> str:
    """File a `claim` of int `amount` against `policy` and return its id.

    FAIL-CLOSED: if `amount` exceeds the policy's coverage the claim is REFUSED and
    NOTHING is written (no cell, no edge) — an over-coverage claim never reaches the Log.
    A filed claim is `status=open`; a `covers` edge ties it to its policy (provenance)."""
    cell = k.weave().get(policy) if isinstance(policy, str) else policy
    if cell is None or cell.type != POLICY:
        raise ValueError(f"file_claim: unknown policy {policy!r}")
    pid = cell.id
    amount = _int("claim amount", amount)
    coverage = int(cell.content["coverage"])
    if amount > coverage:                       # ← the law: REFUSE, write nothing
        raise ValueError(
            f"claim refused: amount {amount} exceeds coverage {coverage} (fail closed)")

    description = nfc(str(description))
    # Content-addressed by (policy, amount, description) so a re-filed identical claim
    # keeps one identity; distinct claims get distinct ids.
    cid = content_id({"claim": pid, "amount": amount, "description": description})
    model.assert_content(k.weft, k.decima_agent_id, cid, CLAIM, {
        "policy": pid, "amount": amount, "description": description, "status": OPEN,
    })
    model.assert_edge(k.weft, k.decima_agent_id, cid, COVERS, pid)
    return cid


def claims(k) -> list:
    """The `claim` Cells on the Weft, in id order."""
    return sorted(k.weave().of_type(CLAIM), key=lambda c: c.id)


# ── payout (Morta-gated reimbursement, posted to the ledger) ────────────────
def approve_payout(k, agent, claim, *, pay_cap, expense_account: str = None,
                   cash_account: str = None) -> dict:
    """Reimburse an OPEN `claim` through PAY1's Morta-gated FINANCIAL rail and post the
    payout to the LEDGER1 ledger, citing the claim. Returns the `payments.pay` result
    augmented with {"claim", "entry"} (the ledger entry id, when paid).

    Composition:
      • `pay_cap` is an approval-gated FINANCIAL capability id (forged by
        `payments.install_rail` and approved via `k.approve`). The payout is DENIED until
        approved and capped by that capability's running spend cap.
      • On a SUCCEEDED payout, a BALANCED journal entry is posted: debit
        `expense_account` (a claims-expense, debit-normal), credit `cash_account`
        (asset ↓), `source` = the signed FINANCIAL receipt, with a `records` edge to it.
        An additional `records` edge from the entry → the claim, and a `pays` edge from
        the claim → the receipt, cite the claim end-to-end.
      • The claim is marked `status=paid` (LWW on its Cell; history on the Log).

    The claim amount was already bounded by coverage at filing (fail closed), so a
    payout can never exceed the policy's coverage."""
    cell = k.weave().get(claim) if isinstance(claim, str) else claim
    if cell is None or cell.type != CLAIM:
        raise ValueError(f"approve_payout: unknown claim {claim!r}")
    cid = cell.id
    amount = int(cell.content["amount"])
    pid = cell.content["policy"]
    policy = k.weave().get(pid)
    payee = policy.content["name"] if policy is not None else cid[:8]

    # Morta-gated, spend-capped, idempotent payout (idempotency keyed on the claim id so
    # a re-approval of the same claim is a no-op replay, never a double payout).
    res = payments.pay(k, agent, pay_cap, amount=amount, payee=f"claim:{payee}",
                       idempotency_key=f"payout:{cid}")
    out = dict(res)
    out["claim"] = cid

    if res.get("status") != payments.executor.SUCCEEDED:        # denied / refused
        return out

    receipt = res["result_cell"]

    # LEDGER1: post a BALANCED entry citing the claim + the signed receipt.
    exp = expense_account if expense_account is not None else \
        accounts.open_account(k, "claims-expense", "expense")
    cash = cash_account if cash_account is not None else \
        accounts.open_account(k, "cash", "asset")
    entry = accounts.post(k, [
        {"account": exp, "side": accounts.DEBIT, "amount": amount},
        {"account": cash, "side": accounts.CREDIT, "amount": amount},
    ], memo=f"insurance payout for claim {cid[:8]}", source=receipt)
    model.assert_edge(k.weft, k.decima_agent_id, entry, accounts.RECORDS, cid)
    model.assert_edge(k.weft, k.decima_agent_id, cid, PAYS, receipt)

    # Mark the claim paid (LWW on its Cell; the open→paid transition is on the Log).
    paid = dict(cell.content)
    paid["status"] = PAID
    paid["receipt"] = receipt
    model.assert_content(k.weft, k.decima_agent_id, cid, CLAIM, paid)

    out["entry"] = entry
    out["receipt"] = receipt
    return out


# ── status ──────────────────────────────────────────────────────────────────
def status(k, claim) -> str:
    """The current `status` of `claim` (open|paid), folded from its Cell."""
    cell = k.weave().get(claim) if isinstance(claim, str) else claim
    if cell is None or cell.type != CLAIM:
        raise ValueError(f"status: unknown claim {claim!r}")
    return cell.content["status"]
