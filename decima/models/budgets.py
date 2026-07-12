"""Budgets — a pre- and post-call gate that STOPS calls when exhausted.

A `Budget` caps tokens and/or cost (micro-cents) for a purpose (an agent, a plan, a
turn). A `BudgetGuard` wraps a `UsageLedger` and enforces the cap:

  * PRE-check — before a call, `check(est_tokens, est_cost)` returns a deny if the
    projected spend would breach a cap. A denied call MUST NOT be made.
  * POST-charge — after a call, `charge(record)` books the real usage; once the
    ledger reaches a cap the budget is `exhausted` and every subsequent `check`
    denies, so no further calls are made.

All numerics are INTS (invariant 6). A budget mints NO authority — it can only
STOP spending, never grant it. Spend never runs autonomously; this gate composes
with the kernel's approval chain downstream (it does not replace it).
"""

from __future__ import annotations

from dataclasses import dataclass

from decima.models.accounting import UsageLedger, UsageRecord


class BudgetExceeded(RuntimeError):
    """Raised by `spend` when a call would breach (or has breached) the budget. The
    call is NOT made — the guard fails closed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Budget:
    """A spend cap. `None` on a dimension means unbounded on that dimension. Ints."""

    token_limit: int | None = None
    cost_limit_microcents: int | None = None

    def __post_init__(self) -> None:
        for name in ("token_limit", "cost_limit_microcents"):
            v = getattr(self, name)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} must be int or None")
            if v < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class BudgetDecision:
    """A pre-check verdict — DATA. `allowed` False ⇒ do not make the call."""

    allowed: bool
    reason: str = ""
    projected_tokens: int = 0
    projected_cost_microcents: int = 0


class BudgetGuard:
    """Enforces a `Budget` over a `UsageLedger`. `check` is the pre-call gate;
    `charge` books actuals; `spend` combines both around a thunk and STOPS (raises)
    when exhausted."""

    def __init__(self, budget: Budget, ledger: UsageLedger | None = None) -> None:
        self.budget = budget
        self.ledger = ledger or UsageLedger()

    @property
    def spent_tokens(self) -> int:
        return self.ledger.total_tokens

    @property
    def spent_cost_microcents(self) -> int:
        return self.ledger.total_cost_microcents

    def remaining_tokens(self) -> int | None:
        if self.budget.token_limit is None:
            return None
        return max(0, self.budget.token_limit - self.spent_tokens)

    def remaining_cost_microcents(self) -> int | None:
        if self.budget.cost_limit_microcents is None:
            return None
        return max(0, self.budget.cost_limit_microcents - self.spent_cost_microcents)

    @property
    def exhausted(self) -> bool:
        """True once either cap is reached — every further `check` denies."""
        token_hit = (
            self.budget.token_limit is not None and self.spent_tokens >= self.budget.token_limit
        )
        cost_hit = (
            self.budget.cost_limit_microcents is not None
            and self.spent_cost_microcents >= self.budget.cost_limit_microcents
        )
        return bool(token_hit or cost_hit)

    def check(self, est_tokens: int = 0, est_cost_microcents: int = 0) -> BudgetDecision:
        """PRE-call gate. Deny if the budget is already exhausted or if this call's
        projected spend would breach a cap. Pure integer comparison."""
        if isinstance(est_tokens, bool) or not isinstance(est_tokens, int):
            raise TypeError("est_tokens must be int")
        if isinstance(est_cost_microcents, bool) or not isinstance(est_cost_microcents, int):
            raise TypeError("est_cost_microcents must be int")

        proj_tokens = self.spent_tokens + est_tokens
        proj_cost = self.spent_cost_microcents + est_cost_microcents

        if self.exhausted:
            return BudgetDecision(False, "budget_exhausted", proj_tokens, proj_cost)
        if self.budget.token_limit is not None and proj_tokens > self.budget.token_limit:
            return BudgetDecision(False, "token_budget_exceeded", proj_tokens, proj_cost)
        if (
            self.budget.cost_limit_microcents is not None
            and proj_cost > self.budget.cost_limit_microcents
        ):
            return BudgetDecision(False, "cost_budget_exceeded", proj_tokens, proj_cost)
        return BudgetDecision(True, "within_budget", proj_tokens, proj_cost)

    def charge(self, record: UsageRecord) -> UsageRecord:
        """POST-call bookkeeping — record the real usage against the budget."""
        return self.ledger.add(record)

    def spend(self, thunk, *, est_tokens: int = 0, est_cost_microcents: int = 0):
        """Run `thunk()` ONLY if the pre-check allows, then charge its
        `(record, value)` return. `thunk` must return `(UsageRecord, result)`. Raises
        `BudgetExceeded` WITHOUT calling `thunk` when the budget would be breached —
        the STOP that keeps a budget-exhausted agent from making more calls."""
        decision = self.check(est_tokens, est_cost_microcents)
        if not decision.allowed:
            raise BudgetExceeded(decision.reason)
        record, result = thunk()
        self.charge(record)
        return result
