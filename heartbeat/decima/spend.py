"""SPEND GOVERNANCE — a Weft-folded spend meter, confirm-charge, quota + scorecards.

VISION "Advanced model strategy — compose, not replace" gives Decima a vendor-neutral
task→TIER router (`decima/router.py`). Routing is ADVICE and confers ZERO authority.
But a routing that lands on a PAID lane (a rented or external-paid provider) implies an
outward *spend* — real money leaving on the user's behalf. Decima already has budget
*caveats* the kernel enforces at `authorize`, and an approval queue (`decima/inbox.py`);
what it lacked was a native SPEND control plane: a budget meter, a confirm-charge gate so
money never leaves autonomously, per-provider quota, and learned per-provider scorecards.

This module is that plane — reimplemented behind Decima contracts, composing over the
existing router / inbox / kernel seams and EDITING no core file. Everything here is:

  • fold-derived from the Weft (charges, dispatches, budget config, quota config are Cells;
    every reported number traces to the signed Cells it summed — Law 4 provenance);
  • ints only — costs in MICRO-CENTS, pressure 0..100, scorecard -100..100, quotas, tokens
    (ints-not-floats; a paid charge is never a float dollar in signed content);
  • offline + deterministic — no network, no wall-clock, no unseeded randomness. Any pacing
    or quota-reset uses an INJECTED logical-time int (`now_tick`, lamport-style), never a
    clock;
  • ZERO ambient authority — the meter mints no grant. A paid/rented charge NEVER happens
    autonomously: it MUST route through the Cycle-48 `ApprovalInbox` + the kernel's
    authorize/Morta gate. The meter records a charge Cell ONLY after `inbox.approve` enacts
    the gated operation and returns ok; if the gate refuses (revoked / ungranted), NOTHING
    is spent. FAIL CLOSED: until a budget is configured, a paid dispatch is denied.

Shared "live status" contract (loose coupling — this lane PRODUCES the `budget` block and
each provider's `quota_remaining` / `scorecard`; the router lane CONSUMES the dict; no lane
imports another lane's module):

    budget = { "remaining_microcents": int, "pressure": int, "configured": bool }
    provider = { ..., "quota_remaining": int, "scorecard": int, ... }
"""
from decima import model
from decima.hashing import content_id, nfc

# ── Weft cell types this lane folds over (all DATA, provenance on the Weft) ───
BUDGET_CFG = "spend_budget"      # the configured budget envelope (LWW singleton)
CHARGE = "spend_charge"          # a money charge, recorded ONLY on human approval
DISPATCH = "spend_dispatch"      # a per-dispatch receipt (decrements quota; carries a score)
QUOTA_CFG = "spend_quota_cfg"    # a per-provider free-tier cap + optional reset boundary

# Privacy tiers that cost money outward → a charge MUST be confirmed before it enacts.
# (Shared contract privacy_tier vocabulary: local_only | private_rented | external |
#  external_paid.) local_only / external (free) dispatches move no money.
PAID_TIERS = ("external_paid", "private_rented")

# A scorecard is ZERO until at least this many outcome samples exist — a learned signal
# never speaks before it has evidence, and never overrides a hard constraint.
SCORECARD_MIN_SAMPLES = 3
SCORE_MIN, SCORE_MAX = -100, 100


class SpendError(Exception):
    """A fail-closed spend refusal (a malformed amount, an unknown item, etc.)."""


def is_paid(privacy_tier: str) -> bool:
    """True iff a dispatch to this privacy tier spends money outward (must be confirmed)."""
    return privacy_tier in PAID_TIERS


def _int(x, what: str) -> int:
    """Signed spend content is an integer — never a float, never a bool-as-int. Fail
    loud (records nothing) on anything else. This is the ints-not-floats law, enforced
    at the door so no float dollar can ever reach a recorded Cell."""
    if not (isinstance(x, int) and not isinstance(x, bool)):
        raise SpendError(f"{what} must be an int (not float/bool), got {x!r}")
    return x


def _clamp(x: int, lo: int, hi: int) -> int:
    return lo if x < lo else hi if x > hi else x


def microcents_for(tokens: int, cost_per_1k_microcents: int) -> int:
    """Cost of `tokens` tokens at `cost_per_1k_microcents` per 1k, in MICRO-CENTS. Pure
    int arithmetic (floor of tokens·rate/1000) — no float dollars ever appear."""
    return _int(tokens, "tokens") * _int(cost_per_1k_microcents, "cost_per_1k") // 1000


class SpendMeter:
    """A spend control plane folded from the Weft. Construct with a `Kernel`; every
    method reads/writes only through the kernel's existing seams (`weave`, `weft`,
    `model.assert_*`) and — for a paid charge — the `ApprovalInbox`. It adds a surface,
    never a new authority path."""

    def __init__(self, k):
        self.k = k
        # A stable principal to author this lane's analytic Cells (the decima agent).
        self._author = k.principal_for(k.weave().get(k.decima_agent_id))

    def _assert(self, cell_id: str, ctype: str, content: dict) -> str:
        model.assert_content(self.k.weft, self._author, cell_id, ctype, content)
        return cell_id

    # ── 1. BUDGET METER ──────────────────────────────────────────────────────
    def configure_budget(self, total_microcents: int, *, per_tick_allowance: int = 0,
                         start_tick: int = 0) -> str:
        """Configure the spend envelope: a total cap (micro-cents) and an optional paced
        per-logical-tick allowance anchored at `start_tick` (an INJECTED logical-time int
        — never a clock). LWW singleton: re-configuring overwrites, the prior config stays
        in history. Until this is called the meter is UNCONFIGURED and a paid dispatch
        fails closed."""
        cid = content_id({"spend_budget": "singleton"})
        return self._assert(cid, BUDGET_CFG, {
            "total_microcents": _int(total_microcents, "total_microcents"),
            "per_tick_allowance": _int(per_tick_allowance, "per_tick_allowance"),
            "start_tick": _int(start_tick, "start_tick"),
            "configured": True,
        })

    def _budget_cfg(self):
        cells = self.k.weave().of_type(BUDGET_CFG)
        return cells[0].content if cells else None

    def is_configured(self) -> bool:
        return self._budget_cfg() is not None

    def _charges(self) -> list:
        """The recorded charge Cells — the signed ground truth every budget number folds
        from. A charge lands ONLY after a human-approved, gate-enacted confirm-charge."""
        return list(self.k.weave().of_type(CHARGE))

    def spent_microcents(self) -> int:
        """Total money spent, folded (summed) from the charge Cells. Pure int."""
        return sum(_int(c.content["microcents"], "microcents") for c in self._charges())

    def remaining_microcents(self) -> int:
        """Budget remaining = total − spent (int). 0 when unconfigured (fail closed)."""
        cfg = self._budget_cfg()
        if cfg is None:
            return 0
        return int(cfg["total_microcents"]) - self.spent_microcents()

    def pressure(self) -> int:
        """Budget pressure, an int in [0,100]: how much of the total is consumed. 0 when
        unconfigured or nothing spent; 100 at/over the cap. Deterministic, int-only."""
        cfg = self._budget_cfg()
        if cfg is None:
            return 0
        total = int(cfg["total_microcents"])
        if total <= 0:
            return 0
        return _clamp(self.spent_microcents() * 100 // total, 0, 100)

    def paced_allowance(self, now_tick: int) -> int:
        """The paced spend allowance AT logical tick `now_tick` (INJECTED — no clock):
        how much the pacing policy permits to have been spent by now, minus what already
        was. `per_tick_allowance · elapsed_ticks` (capped at the total), less spend. A
        negative result means pacing is exceeded. All int; 0 when unconfigured."""
        cfg = self._budget_cfg()
        if cfg is None:
            return 0
        _int(now_tick, "now_tick")
        elapsed = max(0, now_tick - int(cfg["start_tick"]))
        allowed = min(int(cfg["total_microcents"]), int(cfg["per_tick_allowance"]) * elapsed)
        return allowed - self.spent_microcents()

    def budget_block(self) -> dict:
        """The shared status `budget` block: exactly the int-keyed shape the router lane
        consumes. `configured` False ⇒ the consumer must treat paid routing as denied."""
        return {
            "remaining_microcents": self.remaining_microcents(),
            "pressure": self.pressure(),
            "configured": self.is_configured(),
        }

    # ── 2. CONFIRM-CHARGE (no autonomous spend) ──────────────────────────────
    def mint_spend_capability(self, agent_cell, provider_id: str) -> str:
        """Mint (and grant to `agent_cell`) a Morta-gated `spend.charge` capability for a
        provider — the authority a confirm-charge is enacted THROUGH. It carries
        `requires_approval`, so an invoke hits the Morta gate; the ApprovalInbox routes it
        into the queue. The capability is the ONLY thing that carries authority here — the
        meter and inbox carry a human decision to this gate, nothing more."""
        cap_id = self.k._assert_cap(f"spend.charge:{nfc(provider_id)}", "echo",
                                    caveats={"requires_approval": True})
        self.k.grant(cap_id, agent_cell.id)
        return cap_id

    def request_charge(self, inbox, agent_cell, spend_cap_id, *, provider_id: str,
                       tokens: int, cost_per_1k_microcents: int, privacy_tier: str,
                       now_tick: int) -> dict:
        """Propose a PAID dispatch as a confirm-charge. This does NOT spend: it FAILS
        CLOSED if unconfigured, then ENQUEUES an approval item on the Weft describing the
        charge (provider, estimated micro-cents, tokens) via the ApprovalInbox — the money
        leaves only if a human later approves. Returns:
            {"denied": reason}                 — fail closed (unconfigured / over budget), or
            {"queued": item_id, "microcents": int, "tokens": int}

        A non-paid (local / free) tier needs no confirm-charge — call `record_dispatch`."""
        if not is_paid(privacy_tier):
            return {"denied": "not_a_paid_dispatch"}
        # FAIL CLOSED: no budget configured ⇒ a paid dispatch is denied outright.
        if not self.is_configured():
            return {"denied": "budget_not_configured"}
        micro = microcents_for(tokens, cost_per_1k_microcents)
        # A charge that would blow the remaining budget is refused BEFORE it is queued —
        # the budget is a real cap, not a suggestion.
        if micro > self.remaining_microcents():
            return {"denied": "budget_exhausted"}
        desc = (f"charge {micro} microcents to {provider_id} "
                f"({tokens} tok @ {cost_per_1k_microcents}/1k)")
        args = {"provider": nfc(provider_id), "microcents": micro,
                "tokens": _int(tokens, "tokens"), "privacy_tier": privacy_tier,
                "at_tick": _int(now_tick, "now_tick"), "text": desc}
        item_id = inbox.enqueue(agent_cell, spend_cap_id, args, description=desc)
        return {"queued": item_id, "microcents": micro, "tokens": args["tokens"]}

    def approve_charge(self, inbox, item_id) -> dict:
        """Enact a queued confirm-charge: carry the human's approval to the gate. Calls
        `inbox.approve` (which runs the FULL authorize/Morta spine on the pinned op); only
        if it returns ok does the money enact — a `spend_charge` Cell is recorded (Law 4:
        linked to the gate receipt) and the budget decremented by the folded charge.

        If the gate refuses (capability revoked / never granted), NOTHING is spent: no
        charge Cell, the budget is unchanged, and the gate's denial is returned. The meter
        never records a charge the gate did not enact — spend is never ambient."""
        item = inbox.item(item_id)
        if item is None:
            raise SpendError(f"cannot charge: unknown inbox item {item_id!r}")
        args = item.content["args"]
        res = inbox.approve(item_id)          # ← the real Morta/ocap gate decides here
        if "ok" not in res:                   # gate refused → fail closed, spend NOTHING
            return {"denied": res.get("denied", "gate_refused"), "gate": res}
        micro = _int(args["microcents"], "microcents")
        receipt = res.get("result_cell")
        cid = content_id({"spend_charge": item_id, "receipt": receipt})
        self._assert(cid, CHARGE, {
            "provider": args["provider"], "microcents": micro,
            "tokens": _int(args["tokens"], "tokens"),
            "at_tick": _int(args["at_tick"], "at_tick"),
            "item": item_id, "receipt": receipt, "approver": self.k.human.id,
        })
        # Provenance: the money Cell points at the gate receipt that authorized it.
        if receipt is not None:
            model.assert_edge(self.k.weft, self._author, cid, "charged_via", receipt)
        return {"charged": cid, "microcents": micro, "remaining": self.remaining_microcents()}

    def deny_charge(self, inbox, item_id, reason: str = "") -> str:
        """Deny a queued confirm-charge: the inbox records the denial and the charge NEVER
        enacts — no `spend_charge` Cell, the budget is unchanged. Fails closed on an
        unknown / already-decided item (via the inbox)."""
        return inbox.deny(item_id, reason=reason)

    # ── 3. QUOTA (per-provider free-tier cap, folded from dispatch receipts) ──
    def configure_quota(self, provider_id: str, cap_tokens: int, *,
                        reset_boundary: int | None = None) -> str:
        """Set a per-provider free-tier quota cap (int tokens) and an optional reset
        boundary — an INJECTED logical-time int at/after which prior usage no longer
        counts (a quota period rollover). LWW per provider."""
        pid = nfc(provider_id)
        cid = content_id({"spend_quota_cfg": pid})
        content = {"provider": pid, "cap_tokens": _int(cap_tokens, "cap_tokens")}
        if reset_boundary is not None:
            content["reset_boundary"] = _int(reset_boundary, "reset_boundary")
        return self._assert(cid, QUOTA_CFG, content)

    def record_dispatch(self, provider_id: str, *, tokens: int, now_tick: int,
                        score: int | None = None) -> str:
        """Record a dispatch receipt Cell: `tokens` consumed at logical tick `now_tick`
        (INJECTED), optionally carrying an outcome `score` (int in [-100,100]) for the
        provider's learned scorecard. Every dispatch decrements the provider's quota and
        (if scored) feeds its scorecard. Provenance lands on the Weft."""
        pid = nfc(provider_id)
        _int(tokens, "tokens")
        _int(now_tick, "now_tick")
        if score is not None:
            s = _int(score, "score")
            if not (SCORE_MIN <= s <= SCORE_MAX):
                raise SpendError(f"score must be in [{SCORE_MIN},{SCORE_MAX}], got {s}")
        cid = content_id({"spend_dispatch": pid, "tokens": tokens,
                          "tick": now_tick, "head": self.k.weft.head})
        return self._assert(cid, DISPATCH, {
            "provider": pid, "tokens": tokens, "at_tick": now_tick, "score": score,
        })

    def _dispatches(self, provider_id: str) -> list:
        pid = nfc(provider_id)
        return [c for c in self.k.weave().of_type(DISPATCH)
                if c.content.get("provider") == pid]

    def _quota_cfg(self, provider_id: str):
        pid = nfc(provider_id)
        for c in self.k.weave().of_type(QUOTA_CFG):
            if c.content.get("provider") == pid:
                return c.content
        return None

    def consumed_tokens(self, provider_id: str, now_tick: int) -> int:
        """Tokens consumed against a provider's current quota period AS OF logical tick
        `now_tick`, folded from its dispatch receipts. Causal: a dispatch in the future
        (`at_tick > now_tick`) is not yet visible. If a reset boundary has passed
        (`now_tick >= reset_boundary`), only dispatches at/after the boundary count — the
        quota rolled over and prior usage no longer draws it down."""
        _int(now_tick, "now_tick")
        cfg = self._quota_cfg(provider_id)
        boundary = None
        if cfg is not None and "reset_boundary" in cfg and now_tick >= int(cfg["reset_boundary"]):
            boundary = int(cfg["reset_boundary"])
        total = 0
        for c in self._dispatches(provider_id):
            at = int(c.content["at_tick"])
            if at > now_tick:                       # a future dispatch is not yet visible
                continue
            if boundary is None or at >= boundary:  # within the current quota period
                total += int(c.content["tokens"])
        return total

    def quota_remaining(self, provider_id: str, now_tick: int) -> int:
        """Remaining free-tier quota (int tokens, floored at 0), folded from dispatch
        receipts against the configured cap. 0 ⇒ exhausted. Unconfigured ⇒ 0 (a provider
        with no declared quota has no free tier to draw on here)."""
        cfg = self._quota_cfg(provider_id)
        if cfg is None:
            return 0
        return max(0, int(cfg["cap_tokens"]) - self.consumed_tokens(provider_id, now_tick))

    def quota_ok(self, provider_id: str, tokens: int, now_tick: int) -> bool:
        """True iff `tokens` more fit within the provider's remaining quota. When the
        quota is exhausted (remaining 0) this is False — the dispatch is blocked."""
        return self.quota_remaining(provider_id, now_tick) >= _int(tokens, "tokens")

    # ── 4. SCORECARDS (per-provider learned quality, bounded, evidence-gated) ─
    def scorecard(self, provider_id: str) -> int:
        """A per-provider learned-quality signal, a bounded INT in [-100,100], folded from
        scored dispatch outcomes. ZERO until at least `SCORECARD_MIN_SAMPLES` scored
        samples exist (a signal that has not earned its voice stays silent) — and it is
        advisory, never overriding a hard constraint. Deterministic mean, clamped."""
        scores = [int(c.content["score"]) for c in self._dispatches(provider_id)
                  if c.content.get("score") is not None]
        if len(scores) < SCORECARD_MIN_SAMPLES:
            return 0
        return _clamp(sum(scores) // len(scores), SCORE_MIN, SCORE_MAX)

    def provider_status(self, provider_id: str, now_tick: int) -> dict:
        """The two per-provider fields this lane contributes to the shared status dict:
        int `quota_remaining` and int `scorecard`."""
        return {
            "quota_remaining": self.quota_remaining(provider_id, now_tick),
            "scorecard": self.scorecard(provider_id),
        }
