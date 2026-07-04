"""MAILPOLL — the recurring driver: `mail_engine.receive` gets a CALLER.

MAILWIRE (`mail_engine.py`) made inbound mail arrive through the gated egress
transport as untrusted DATA — but `receive(...)` had NO recurring caller: it
only ran when something explicitly invoked it. This module is that caller.
It makes inbound mail ALWAYS-ON: `schedule_poll` arms a recurring poll that
`reactor.tick` (driven, in turn, by `daemon.advance`/`do_beat`) fires on its
own, every `interval` logical ticks, without a single explicit `receive` call
from any operator or check.

REACTOR1's tick drives exactly three lanes — watchers, SCHED1 events, JOBS1
jobs — and does not (and must not) grow a fourth. SCHED1's own recurring idiom
(`fire` reschedules a repeating event to `at + repeat_every`) always ROUTES its
action through `disposition.dispose(title, ...)` — perfect for a reminder whose
"action" is a piece of text, wrong for a poll whose action is a real Python call
into the gated transport with a specific (agent_cell, cap_id, transport). So
this lane composes JOBS1 instead: `schedule_poll` enqueues ONE durable job
whose freshly-registered, lane-owned effect (never `'echo'`) does two things
when the beat runs it — (1) calls `poll_once`, which is nothing but
`mail_engine.receive` under an unwrapped name, THROUGH the gated transport
`cap_id` names, folding every fetched message into the digest as untrusted DATA
exactly as `receive` already does; and (2) enqueues its OWN successor job at
`run_at + interval` — the SAME "a fired repeating event reschedules itself"
idiom SCHED1 uses for reminders, reimplemented here over JOBS1 because the
action is a real call, not routed text. `reactor.tick`/`jobs.due`/`jobs.run`
fire it exactly like any other durable job; neither `reactor.py`, `jobs.py`
nor `scheduling.py` is edited or needs to be.

Laws upheld:
  - NO AMBIENT AUTHORITY. `schedule_poll` mints only a harmless "run the poll"
    job capability (no `requires_approval`, confers no authority beyond
    triggering this lane's own effect) — it is NOT the mail capability. The
    ONLY authority that ever touches the network is `cap_id`, the CALLER-SUPPLIED,
    separately Morta-gated egress capability `poll_once`/`mail_engine.receive`
    already require; an unwired poll (no transport, no live/approved `cap_id`)
    fails CLOSED exactly as `mail_engine.receive` does — this lane confers
    nothing new.
  - UNTRUSTED CONTENT IS DATA. Every message the poll fetches lands through
    `mail_engine.receive` → `maildigest.ingest_email`, `instruction_eligible=
    False` on both Cells, exactly as an explicit `receive` call would; polling
    on the beat changes WHEN mail arrives, never WHAT it is trusted to do.
  - DETERMINISM / INTS-NOT-FLOATS. `interval`/`run_at`/`window` are LOGICAL int
    ticks — the caller (the tick's `now`) owns the clock; a float/bool is
    rejected at the door before it reaches signed content. No wall-clock, no
    unseeded randomness anywhere in this module.
  - EVERYTHING ON THE WEFT. Each occurrence is a `job` Cell (JOBS1) with its own
    pre-fixed lease; the reschedule is a fresh `job` Cell too — the whole
    recurring chain is an ordinary, auditable subgraph of Cells/edges, not a
    single mutable in-process variable.

Composes ONLY public APIs (`executor.register`, `jobs.enqueue`, `k._assert_cap`,
`k.grant`, `mail_engine.receive`, `maildigest.MAIL_SCOPE`) — no core file, no
seam module (`reactor.py`, `jobs.py`, `scheduling.py`, `mail_engine.py`), is
edited.

Pure stdlib. Proof: heartbeat/checks/482_mailpoll.py.
"""
from __future__ import annotations

from decima import executor
from decima import jobs
from decima import mail_engine
from decima import maildigest
from decima import wire

#: The mail source this lane polls by default — a check may override it, but it
#: must always be an https endpoint (mail_engine.receive refuses anything else).
DEFAULT_ENDPOINT = "https://mail.internal/inbox"

#: The default job-family name a poll is registered under (distinguishes several
#: concurrently-scheduled polls, e.g. against different mailboxes/cap ids).
POLL_JOB_NAME = "mailpoll"

#: A generous default lease window (JOBS1 `enqueue(..., window=...)`). NOTE: the
#: lease's `expires_at` is checked against `k.weft.lamport` — the WEFT'S OWN
#: append counter, which advances on every Cell/edge appended anywhere (not the
#: logical `run_at`/`interval` ticks this lane schedules occurrences at) — so the
#: window must be generous enough to outlive however many appends land on the
#: Weft between one occurrence's enqueue and its actual firing, independent of
#: `interval`. A caller with a tighter/looser durability need may still pass its
#: own `window=`.
DEFAULT_WINDOW = 100_000


def _int_tick(name: str, v) -> int:
    """Reject floats/bools — every tick this lane touches is a LOGICAL int; the
    caller owns the clock, never a wall-clock, here or anywhere downstream."""
    if not isinstance(v, int) or isinstance(v, bool):
        raise TypeError(f"{name} must be an int logical tick, got {type(v).__name__}")
    return int(v)


def poll_once(k, agent_cell, cap_id, *, transport, endpoint: str = DEFAULT_ENDPOINT,
             scope: str = maildigest.MAIL_SCOPE) -> dict:
    """The single receive step the recurring driver runs each period — so a
    check can drive it deterministically without going through the job/tick
    machinery at all.

    This is nothing but `mail_engine.receive` under a poll-shaped name: it
    fetches THROUGH the gated transport `cap_id` names (an unwired poll — no
    injected `transport` and no live, granted, Morta-approved egress capability
    — fails CLOSED with `live_wire.NoGatedTransport`/`wire.EgressDenied`,
    exactly as `receive` does, before any socket), and folds every fetched
    message into the digest as an untrusted OBSERVATION
    (`instruction_eligible=False`). Polling confers NO authority of its own —
    the fetch's authority is entirely `cap_id`'s, pre-fixed by the caller.

    Returns {"received": int, "messages": [ingest results]}."""
    res = mail_engine.receive(k, agent_cell, cap_id, endpoint=endpoint,
                              transport=transport, scope=scope)
    return {"received": int(res["received"]), "messages": res["messages"]}


def _enqueue_occurrence(k, agent_cell, cap_id, *, transport, endpoint, scope,
                        name, run_at, interval, window) -> str:
    """Enqueue ONE occurrence of the recurring poll as a durable job (JOBS1).

    A fresh, lane-owned effect — its name folds in `name`/`cap_id`/`run_at` so
    two occurrences (or two differently-configured polls) never collide in the
    shared executor registry — closes over exactly this occurrence's
    (run_at, interval): when the beat runs the job, the handler (1) calls
    `poll_once` — THE load-bearing call into `mail_engine.receive` — and then
    (2) enqueues its OWN successor at `run_at + interval`, mirroring SCHED1's
    own "a fired repeating event reschedules itself" idiom. The job's own
    capability is unapproved-by-default and confers nothing beyond "run this
    lane's poll effect" — it is NOT the mail capability."""
    effect = f"mailpoll_probe:{name}:{cap_id}:{run_at}"

    def _handler(impl, args):
        received, refusal = 0, None
        try:
            # LOAD-BEARING: this is the ONE call that makes the beat receive
            # mail. Replace/neuter this line (e.g. drop the call, or stub it
            # to {"received": 0}) and the beat stops receiving mail on its
            # own — no new message is ever ingested by a mere tick, only by
            # an explicit manual receive() call again.
            res = poll_once(k, agent_cell, cap_id, transport=transport,
                            endpoint=endpoint, scope=scope)
            received = res["received"]
        except (wire.EgressDenied, mail_engine.MailEngineError) as e:
            # The mail cap is not (yet, or no longer) live/approved — the SAME
            # fail-closed refusal `mail_engine.receive` itself raises. Turn it
            # into a definite executor failure (never a crashed tick) so the
            # durable job records FAILED and no message is stored — polling
            # confers no authority to bypass the mail capability's own gate.
            refusal = str(e)
        finally:
            # The recurring occurrence reschedules its OWN successor
            # regardless of whether THIS fetch succeeded or was refused — a
            # transiently-ungated poll keeps trying every `interval`, it does
            # not silently stop being scheduled.
            rescheduled = _enqueue_occurrence(
                k, agent_cell, cap_id, transport=transport, endpoint=endpoint,
                scope=scope, name=name, run_at=run_at + interval,
                interval=interval, window=window)
        if refusal is not None:
            raise executor.ExecError(f"mailpoll: poll refused — {refusal}")
        return {"out": f"received:{received}", "received": received,
               "rescheduled": rescheduled}

    executor.register(effect, _handler)
    job_cap = k._assert_cap(effect, effect)
    k.grant(job_cap, k.decima_agent_id)
    return jobs.enqueue(k, f"{name}:{run_at}", capability=job_cap, run_at=run_at,
                        max_uses=1, window=window)


def schedule_poll(k, agent_cell, cap_id, *, interval: int, run_at: int = 0,
                  window: int | None = None, transport=None,
                  endpoint: str = DEFAULT_ENDPOINT, scope: str = maildigest.MAIL_SCOPE,
                  name: str = POLL_JOB_NAME) -> str:
    """Register a RECURRING poll of `mail_engine.receive` so inbound mail is
    received on every beat of the reactor's tick, not just on an explicit call.

    `(agent_cell, cap_id)` is the SAME gated-transport pair `mail_engine.receive`
    takes — the poll uses ONLY that pre-fixed, separately Morta-gated mail
    capability; it mints no new authority over the network. `transport` is the
    injectable offline-stub seam (mirroring every wrapped engine): a real
    deployment omits it so `mail_engine.receive` builds
    `live_wire.gated_transport(k, agent_cell, cap_id)` itself; a check injects a
    stub so the oracle proves the whole recurring wiring offline.

    `interval` is a positive int number of logical ticks between polls;
    `run_at` (default 0) is the first tick the poll becomes due; `window`
    (default `DEFAULT_WINDOW`) is the durable job's lease-validity window
    (JOBS1 `enqueue(..., window=...)`) — checked against the WEFT's own append
    counter (`k.weft.lamport`), not `interval`, so the default is generous.
    All three are LOGICAL int ticks — a float/bool is rejected before anything
    is enqueued or signed (DETERMINISM, ints-not-floats).

    Returns the id of the FIRST occurrence's `job` Cell. From then on, every
    firing reschedules its own successor — a `reactor.tick`/`daemon.advance`
    sweep across the frontiers needs no further caller-side bookkeeping."""
    interval = _int_tick("interval", interval)
    if interval <= 0:
        raise ValueError("interval must be a positive number of logical ticks")
    run_at = _int_tick("run_at", run_at)
    window = DEFAULT_WINDOW if window is None else _int_tick("window", window)
    if window <= 0:
        raise ValueError("window must be a positive number of logical ticks")
    return _enqueue_occurrence(k, agent_cell, cap_id, transport=transport,
                               endpoint=endpoint, scope=scope, name=name,
                               run_at=run_at, interval=interval, window=window)
