"""RESILIENCE1 — backpressure / rate-limit / circuit-breaker / bulkhead around
every outward INVOKE (CAPABILITY_MAP B3).

ocap says a principal MAY invoke an effect; the autonomy ladder says how
autonomously; the sandbox says what it may touch. RESILIENCE1 is the orthogonal
fourth seam: *how fast and how often* an already-authorized outward effect may
fire before the system protects itself. It wraps a thunk that performs the real,
already-authorized invoke — it NEVER widens authority and never invokes anything
the caller could not already invoke. It only ever REFUSES (fails fast) or lets
the thunk through.

The three patterns, per effect key (an opaque string the caller chooses — e.g.
"effect" or "effect|principal"):

  - circuit_breaker: after N consecutive FAILED/UNKNOWN receipts a breaker OPENS
    and subsequent calls fail FAST without invoking. After a `cooldown` of logical
    ticks it HALF-OPENs and admits ONE trial; a SUCCEEDED trial CLOSES it, any
    other result re-OPENS it. (Classic three-state breaker.)
  - rate_limit: an integer token-bucket per key — `budget` tokens per `window`
    logical ticks; a call over budget in the current window is refused.
  - bulkhead: a max-concurrent cap per key — at most `limit` calls in flight at
    once; the (limit+1)-th is refused until one releases.

LAWS (heartbeat/checks/README + B3):
  - DETERMINISM: time is a logical int `now` the caller passes (lamport ticks),
    NEVER wall-clock. All thresholds/budgets are ints. The breaker/bucket/bulkhead
    state is a pure function of the calls made — re-derives identically.
  - AUDITABILITY: every breaker STATE CHANGE (open / half-open / close) is recorded
    on the Weft as a typed `breaker` cell, so the resilience posture is a fold over
    the Log, never an ambient toggle.
  - NEVER weakens authorization: `guard` wraps a thunk that itself runs the real,
    already-authorized invoke; resilience can only refuse, never grant.

Composes model/weave/weft PUBLIC APIs only (model.assert_content / k.weave /
k.weft.lamport). No core edit.
"""
from decima.model import assert_content
from decima.hashing import content_id, nfc
from decima import executor

# Breaker states.
CLOSED = "CLOSED"       # healthy — calls pass through
OPEN = "OPEN"           # tripped — calls fail fast without invoking
HALF_OPEN = "HALF_OPEN"  # cooling done — admit ONE trial

# The receipt statuses that count as a breaker "failure" (B3): a definite no-effect
# error AND the honest "I don't know" both erode the breaker — an UNKNOWN outcome is
# exactly the dropped-connection / timeout case a breaker exists to shed load from.
_BAD = (executor.FAILED, executor.UNKNOWN)

# Refusal reason codes (returned, not raised — a fail-fast refusal is a value).
R_OPEN = "breaker_open"
R_RATE = "rate_limited"
R_BULK = "bulkhead_full"

_CELL = "breaker"   # Weft cell type for an audited breaker state change


def _key(effect_key: str) -> str:
    return nfc(str(effect_key))


# ── circuit breaker ─────────────────────────────────────────────────────────
class CircuitBreaker:
    """An int-deterministic three-state breaker. `now` is a logical tick.

    State lives in memory (the live posture), and every TRANSITION is mirrored to
    the Weft via `_record` so the history is auditable + foldable. `threshold` and
    `cooldown` are ints. No wall-clock anywhere.
    """

    def __init__(self, k, threshold: int = 3, cooldown: int = 10):
        if threshold < 1:
            raise ValueError("breaker threshold must be >= 1")
        if cooldown < 0:
            raise ValueError("breaker cooldown must be >= 0")
        self.k = k
        self.threshold = int(threshold)
        self.cooldown = int(cooldown)
        # per-key: {"state", "fails", "opened_at", "trial"}
        self._s: dict[str, dict] = {}

    def _slot(self, key: str) -> dict:
        return self._s.setdefault(
            key, {"state": CLOSED, "fails": 0, "opened_at": None, "trial": False})

    def state(self, key: str, *, now: int) -> str:
        """The live state at logical `now`, applying the cooldown→half-open clock.
        Pure read: an OPEN breaker whose cooldown has elapsed reports HALF_OPEN
        (and records the transition once)."""
        s = self._slot(_key(key))
        if s["state"] == OPEN and now - s["opened_at"] >= self.cooldown:
            self._transition(_key(key), HALF_OPEN, now=now)
            s["trial"] = False
        return s["state"]

    def allow(self, key: str, *, now: int) -> bool:
        """May a call proceed under the breaker at `now`? CLOSED → yes. OPEN → no
        (fail fast). HALF_OPEN → admit exactly ONE trial; further calls wait."""
        key = _key(key)
        st = self.state(key, now=now)
        if st == CLOSED:
            return True
        if st == OPEN:
            return False
        # HALF_OPEN: exactly one trial in flight.
        s = self._slot(key)
        if s["trial"]:
            return False
        s["trial"] = True
        return True

    def record(self, key: str, status: str, *, now: int) -> None:
        """Feed a receipt status back into the breaker after a trial/call ran.
        A SUCCEEDED in HALF_OPEN closes it; N consecutive bad in CLOSED opens it;
        any bad in HALF_OPEN re-opens it."""
        key = _key(key)
        s = self._slot(key)
        bad = status in _BAD
        if s["state"] == HALF_OPEN:
            s["trial"] = False
            if bad:
                self._open(key, now=now)
            else:
                self._close(key, now=now)
            return
        # CLOSED
        if bad:
            s["fails"] += 1
            if s["fails"] >= self.threshold:
                self._open(key, now=now)
        else:
            if s["fails"]:
                s["fails"] = 0  # a success resets the consecutive-failure run

    def _open(self, key: str, *, now: int) -> None:
        s = self._slot(key)
        s["state"], s["opened_at"], s["trial"] = OPEN, int(now), False
        self._transition(key, OPEN, now=now)

    def _close(self, key: str, *, now: int) -> None:
        s = self._slot(key)
        s["state"], s["fails"], s["opened_at"], s["trial"] = CLOSED, 0, None, False
        self._transition(key, CLOSED, now=now)

    def _transition(self, key: str, to: str, *, now: int) -> None:
        s = self._slot(key)
        s["state"] = to
        self._record(key, to, now=now)

    def _record(self, key: str, to: str, *, now: int) -> str:
        """Mirror a breaker state change onto the Weft as a typed `breaker` cell —
        the auditable trail (B3). Content-addressed by (key, state, now) so the
        fold is deterministic and a replay lands on the same cell."""
        cid = content_id({"breaker": key, "state": to, "at": int(now)})
        assert_content(self.k.weft, self.k.decima.id, cid, _CELL,
                       {"effect_key": key, "state": to, "at": int(now)})
        return cid


# ── rate limit (integer token bucket) ───────────────────────────────────────
class RateLimiter:
    """A per-key integer token bucket: `budget` tokens per `window` logical ticks.
    No floats — the window is a logical interval [start, start+window). A call in a
    new window resets the bucket; over-budget within a window is refused."""

    def __init__(self, budget: int = 5, window: int = 10):
        if budget < 0:
            raise ValueError("rate budget must be >= 0")
        if window < 1:
            raise ValueError("rate window must be >= 1")
        self.budget = int(budget)
        self.window = int(window)
        self._b: dict[str, dict] = {}   # key -> {"start", "used"}

    def _win(self, now: int) -> int:
        return (int(now) // self.window) * self.window

    def take(self, key: str, *, now: int) -> bool:
        """Try to consume one token at `now`. True if granted (a token was spent),
        False if the budget for the current window is exhausted (refuse)."""
        key = _key(key)
        start = self._win(now)
        b = self._b.get(key)
        if b is None or b["start"] != start:
            b = {"start": start, "used": 0}
            self._b[key] = b
        if b["used"] >= self.budget:
            return False
        b["used"] += 1
        return True


# ── bulkhead (max concurrent) ───────────────────────────────────────────────
class Bulkhead:
    """A per-key max-concurrent cap. `acquire` reserves a slot (False if full);
    `release` frees one. Pairs around a single guarded call so an effect can never
    have more than `limit` calls in flight."""

    def __init__(self, limit: int = 4):
        if limit < 1:
            raise ValueError("bulkhead limit must be >= 1")
        self.limit = int(limit)
        self._n: dict[str, int] = {}

    def in_flight(self, key: str) -> int:
        return self._n.get(_key(key), 0)

    def acquire(self, key: str) -> bool:
        key = _key(key)
        if self._n.get(key, 0) >= self.limit:
            return False
        self._n[key] = self._n.get(key, 0) + 1
        return True

    def release(self, key: str) -> None:
        key = _key(key)
        n = self._n.get(key, 0)
        if n > 0:
            self._n[key] = n - 1


# ── the composed guard ──────────────────────────────────────────────────────
class Resilience:
    """Composes breaker + rate-limit + bulkhead around a thunk. One instance per
    kernel holds the live posture for every effect key it has seen."""

    def __init__(self, k, *, threshold: int = 3, cooldown: int = 10,
                 budget: int = 5, window: int = 10, max_concurrent: int = 4):
        self.k = k
        self.breaker = CircuitBreaker(k, threshold=threshold, cooldown=cooldown)
        self.rate = RateLimiter(budget=budget, window=window)
        self.bulkhead = Bulkhead(limit=max_concurrent)

    def guard(self, effect_key: str, call, *, now: int) -> dict:
        """Apply breaker → rate-limit → bulkhead around `call` (a 0-arg thunk that
        performs the real, already-authorized invoke and returns its result dict).

        Order matters: a tripped breaker should fail fast WITHOUT even spending a
        token (it is shedding load), so the breaker is checked first; the bucket
        next (cheap, no slot held); the bulkhead last (it holds a slot for the
        duration of the call). Returns the thunk's result on success, or a
        fail-fast refusal `{"refused": <code>, "reason": ...}` — the effect did
        NOT run. The breaker is fed the receipt status so it can trip / recover."""
        key = _key(effect_key)

        # 1. circuit breaker — fail fast if OPEN (or half-open trial already taken).
        if not self.breaker.allow(key, now=now):
            st = self.breaker.state(key, now=now)
            return {"refused": R_OPEN, "reason": f"circuit breaker {st} for {key!r}",
                    "state": st, "effect_key": key}

        # 2. rate limit — refuse an over-budget call in this window.
        if not self.rate.take(key, now=now):
            return {"refused": R_RATE,
                    "reason": f"rate limit: over {self.rate.budget}/{self.rate.window} "
                              f"for {key!r}",
                    "effect_key": key}

        # 3. bulkhead — refuse past the concurrency cap.
        if not self.bulkhead.acquire(key):
            return {"refused": R_BULK,
                    "reason": f"bulkhead full ({self.bulkhead.limit} concurrent) for {key!r}",
                    "effect_key": key}

        # Authorized + admitted: run the real invoke. The breaker observes its
        # receipt status (so a flaky effect trips it); the bulkhead slot is always
        # released, even if the thunk raises.
        try:
            result = call()
        finally:
            self.bulkhead.release(key)

        status = _status_of(result)
        self.breaker.record(key, status, now=now)
        return result


def _status_of(result) -> str:
    """Pull the receipt status out of a guarded call's result. The kernel's
    invoke() returns {"ok": <receipt>, "status": ...} on success and
    {"denied": ..., "status": ...} on refusal; a raw receipt carries `status`
    directly. Default SUCCEEDED for an opaque truthy result with no status."""
    if isinstance(result, dict):
        if "status" in result:
            return result["status"]
        ok = result.get("ok")
        if isinstance(ok, dict) and "status" in ok:
            return ok["status"]
    return executor.SUCCEEDED


def attach(k, **kw) -> Resilience:
    """Convenience: build a Resilience for a kernel (the seam an app/agent uses)."""
    return Resilience(k, **kw)
