"""LIVEWORLD1 — the live-world test harness: deterministic fault injection over the
gated live path (Phase 4 · the named roadmap item, now real).

The roadmap names a "live-world test harness" that never existed: Phase 1 armed the
wire (`wire.py`), Phase 2 made the gate the ONLY live path (`live_wire.py`), and every
wrapped engine proves its happy path offline through an injected fake socket — but
nothing ever drove the gated path through ADVERSE conditions and proved the system
degrades HONESTLY. That is the gap between "the gate works" and "the gate holds when
the world misbehaves": a timeout after submission, a refused connection, a tampered or
truncated response, a grant revoked between approval and the call. Under every one of
those, the Five-Laws answer is fixed:

  * the receipt tells the TRUTH (WEFT §8.3 / executor.Ambiguous): an unobservable
    outcome is UNKNOWN, a definite no-effect is FAILED — NEVER a fabricated SUCCEEDED;
  * the GATE holds: a revoked capability is refused at the wire before any socket
    (fail closed, a `wire_decision` DENY Cell as provenance), and a bare/ungated path
    raises — stress opens no bypass;
  * nothing LEAKS: the credential a faulted request carried — even one an adversarial
    response reflects straight back — never lands raw on the Weft.

This module is that harness, composed over PUBLIC APIs only (wire / live_wire /
executor / kernel):

  `fault_open(fault, ...)`   — a battery of injectable FAULT sockets for the `_open`
                               seam of `wire.real_transport` / `live_wire.gated_*` —
                               the SAME seam every wrapped-engine offline check uses.
                               Each is a deterministic stand-in for a real network
                               fault: NO real socket, NO DNS, NO wall-clock (a
                               "timeout" raises immediately; nothing sleeps).
  `probe_handler(...)`       — the honest hermetic probe ENGINE (`lw_probe`): the
                               wrapped-engine idiom in miniature — credential applied
                               INSIDE (dispense, don't disclose), one call through the
                               gated transport, outcome mapped to the truthful status.
  `scenario(k, agent_cell, cap_id, fault, args)`
                             — run ONE effect through the FULL gated path (grant →
                               Morta approval → per-call rule of egress → provenance
                               Cell → faulted socket → kernel receipt) and return the
                               honest outcome {status, receipt, gate_held, leaked, …}.
  `honest(fault, outcome)`   — the load-bearing VERDICT: an injected fault whose
                               receipt reads SUCCEEDED is FABRICATED; so is a leak or
                               a gate breach. The harness never rewrites a status — it
                               only JUDGES the receipt the kernel recorded, so a lying
                               engine shows up as fabricated, never as repaired.
  `run_suite(k, agent_cell, cap_id)`
                             — the full battery; returns int counts
                               {scenarios, honest, fabricated, denials} (fabricated is
                               expected to be 0 — a rise is a caught regression).

DETERMINISTIC + OFFLINE: every fault is a pure function of its inputs; "time" never
appears (the injected timeout raises without sleeping); the only randomness is the
kernel's own invoke nonce (pre-existing). INTS-NOT-FLOATS: the healthy probe carries
an int logical tick; a float tick in a response is refused at the door and READS AS
TAMPER. UNTRUSTED CONTENT IS DATA: the probe's recorded output is
instruction_eligible=False, and a faulted body is never recorded at all (it may
reflect a credential). Proof: heartbeat/checks/454_liveworld.py.
"""
import json

from decima import executor, live_wire, wire

PROBE_EFFECT = "lw_probe"          # the harness's OWN hermetic effect — never 'echo'
PROBE_TICK = 7                     # the healthy ack's logical tick — an int, never a clock

# ── the fault vocabulary ───────────────────────────────────────────────────────
NONE = "NONE"                                  # healthy control — no injected fault
TIMEOUT = "TIMEOUT"                            # submitted, then the wire went silent
CONNECTION_REFUSED = "CONNECTION_REFUSED"      # the connection never opened
TAMPERED_RESPONSE = "TAMPERED_RESPONSE"        # a corrupt 200 that reflects the credential
PARTIAL_RESPONSE = "PARTIAL_RESPONSE"          # a 200 with the required fields missing
REVOKED_MID_FLIGHT = "REVOKED_MID_FLIGHT"      # approved, then revoked before the call

# The full battery, in the ONE runnable order: REVOKED_MID_FLIGHT is LAST because
# Morta's RETRACT is permanent on an append-only history — after it the grant is
# dead and the suite has consumed it. A fresh battery needs a fresh grant.
FAULTS = (NONE, TIMEOUT, CONNECTION_REFUSED, TAMPERED_RESPONSE,
          PARTIAL_RESPONSE, REVOKED_MID_FLIGHT)


class LiveWorldError(RuntimeError):
    """Harness misuse (an unknown fault, a cap with no allowlist, REVOKED_MID_FLIGHT
    on a never-approved cap) — or a broken invariant INSIDE the harness itself, e.g.
    the socket running for a revoked capability (a gate breach). Raised LOUD; the
    harness fails closed rather than classify a scenario it cannot stand behind."""


# ── the injectable fault sockets ───────────────────────────────────────────────
def fault_open(fault, *, secret: str = "", calls: list | None = None, witness=None):
    """Forge a deterministic FAULT socket for the `_open` seam of
    `wire.real_transport` / `live_wire.gated_transport` — the exact seam every
    wrapped-engine offline check injects. It replaces the SOCKET, never the gate:
    the full rule of egress (allowlist · Morta · `wire_decision` provenance) runs
    BEFORE it, real or fake. Each fault is a stand-in for a real network failure —
    no real socket, no DNS lookup, no wall-clock (the "timeout" raises immediately):

      NONE               — healthy control: a well-formed ack `{probe:"ok", tick:int}`;
      TIMEOUT            — raises TimeoutError AFTER submission: the request left the
                           box, the outcome is unobservable (→ UNKNOWN, never invented);
      CONNECTION_REFUSED — raises ConnectionRefusedError: the connection never opened,
                           nothing reached the world (→ a definite FAILED);
      TAMPERED_RESPONSE  — a 200 whose body is corrupt (a FLOAT tick — floats are
                           refused at the door, so corruption reads as tamper) and
                           which REFLECTS the caller's credential back (an adversarial
                           echo the engine must never record);
      PARTIAL_RESPONSE   — a 200 with an empty body: the required fields are missing;
      REVOKED_MID_FLIGHT — must NEVER be reached: the gate refuses a revoked cap
                           before any socket. Reaching it is a GATE BREACH and raises
                           LiveWorldError LOUD (and the scenario reads gate_held=False).

    `calls` is in-memory telemetry only — one entry per socket call, recording whether
    the credential rode the request (`auth_applied`, a bool — never the secret itself)
    and `witness()` sampled at socket time (the seam that proves the ALLOW decision
    landed BEFORE the socket ran). Nothing here is ever recorded on the Weft."""
    if fault not in FAULTS:
        raise LiveWorldError(f"unknown fault {fault!r} (known: {', '.join(FAULTS)})")
    calls = calls if calls is not None else []

    def _open(url, headers, body, method, timeout):
        calls.append({
            "url": str(url), "method": str(method),
            # the credential's PRESENCE is telemetry; the credential itself never is.
            "auth_applied": bool(secret) and secret in str(headers or {}),
            "allows_at_socket": witness() if witness is not None else None,
        })
        if fault == NONE:
            return 200, {"probe": "ok", "tick": PROBE_TICK}
        if fault == TIMEOUT:
            raise TimeoutError("liveworld: injected timeout after submission "
                               "(deterministic — nothing slept, no wall-clock)")
        if fault == CONNECTION_REFUSED:
            raise ConnectionRefusedError("liveworld: injected connection refused — "
                                         "the connection never opened")
        if fault == TAMPERED_RESPONSE:
            # A corrupt 200 that ALSO reflects the credential back at the caller: the
            # engine must refuse the float tick at the door and never record the echo.
            return 200, {"probe": "ok", "tick": 7.5,
                         "echo": f"Bearer {secret}" if secret else "Bearer <none>"}
        if fault == PARTIAL_RESPONSE:
            return 200, {}
        # REVOKED_MID_FLIGHT: the gate must have refused before any socket existed.
        raise LiveWorldError("liveworld: GATE BREACH — the socket ran for a revoked "
                             "capability (the gate must refuse first, fail closed)")

    _open.calls = calls
    _open.fault = fault
    return _open


# ── the honest hermetic probe engine ───────────────────────────────────────────
def probe_handler(transport, url: str, secret: str):
    """Build the HONEST `lw_probe` engine — the wrapped-engine idiom (shipping / sms /
    weather) in miniature: the credential is applied INSIDE the handler (dispense,
    don't disclose — it never rides the INVOKE args, the receipt, or the Weft), one
    call goes through the gated transport, and the outcome maps to the truthful
    receipt status per WEFT §8.3 (the tracing rule — an unobservable outcome is NEVER
    fabricated as SUCCEEDED):

      transport raises EgressDenied       → ExecError  (FAILED — the gate refused
                                            before any socket; a definite no-effect)
      raises ConnectionRefusedError       → ExecError  (FAILED — the connection never
                                            opened; nothing reached the world)
      raises anything else (e.g. timeout) → Ambiguous  (UNKNOWN — submitted, outcome
                                            unobservable; never invented)
      non-dict / tampered / partial body  → Ambiguous  (UNKNOWN — never trusted, and
                                            NEVER echoed: a tampered body can reflect
                                            the credential straight back)
      4xx                                 → ExecError  (FAILED — a definite rejection)
      200 well-formed (`probe:"ok"`, an   → the ONLY SUCCEEDED; the ack is recorded
      INT tick — a float reads as tamper)   as DATA (instruction_eligible=False).
    """
    headers = {"Accept": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"     # applied here, never returned
    body = json.dumps({"probe": "liveworld"}, sort_keys=True, separators=(",", ":"))

    def handler(_impl, _args):
        try:
            status, resp = transport(url, headers, body)
        except wire.EgressDenied as e:
            # The gate refused BEFORE any socket: fail closed, a definite no-effect.
            raise executor.ExecError(f"lw_probe: egress denied at the wire — {e}")
        except ConnectionRefusedError as e:
            raise executor.ExecError(
                f"lw_probe: connection refused, nothing reached the world — {e}")
        except Exception as e:
            raise executor.Ambiguous(
                f"lw_probe: transport fault after submission, outcome unobservable — {e}")
        if not isinstance(resp, dict):
            raise executor.Ambiguous(
                f"lw_probe: unparseable response (http {status}) — outcome unobservable")
        tick = resp.get("tick")
        if status == 200 and resp.get("probe") == "ok" \
                and isinstance(tick, int) and not isinstance(tick, bool):
            return {"out": "probe acknowledged", "tick": tick,
                    "instruction_eligible": False, "untrusted": True}
        if isinstance(status, int) and 400 <= status < 500:
            raise executor.ExecError(
                f"lw_probe: rejected (http {status}) — a definite no-effect")
        # Partial / tampered / unexpected: never trusted, never echoed (the body may
        # carry a reflected credential); the outcome stays honestly unobservable.
        raise executor.Ambiguous(
            f"lw_probe: partial or tampered response (http {status}) — outcome "
            "unobservable; body withheld (untrusted)")

    return handler


# ── weft inspection (read-only) ────────────────────────────────────────────────
def _decision_counts(k, cap_id) -> tuple:
    """(allow, deny) counts of `wire_decision` Cells this capability has produced —
    the gate's own provenance, folded read-only from the Weave."""
    allow = deny = 0
    for c in k.weave().of_type(wire.WIRE_DECISION):
        if c.content.get("capability") == cap_id:
            if c.content.get("decision") == wire.ALLOW:
                allow += 1
            elif c.content.get("decision") == wire.DENY:
                deny += 1
    return allow, deny


def secret_on_weft(k, secret: str) -> bool:
    """True iff the RAW secret string appears anywhere in recorded history — every
    event body on the Weft (a superset of every cell's content, including INVOKE
    args and receipt payloads). Read-only; the walk itself re-verifies id+signature
    per event (tamper-evidence for free). Empty secret → False (nothing to leak)."""
    if not secret:
        return False
    for ev in k.weft.events():
        if secret in str(ev.body):
            return True
    return False


# ── one scenario through the full gated path ───────────────────────────────────
def scenario(k, agent_cell, cap_id, fault, args=None, *, _handler=None) -> dict:
    """Run ONE hermetic effect (`lw_probe`) through the FULL gated live path — grant
    in the acting agent's envelope → Morta approval → the per-call rule of egress →
    `wire_decision` provenance Cell → the injected FAULT socket → the kernel's
    receipt — and return the honest outcome:

        {fault, status, receipt, gate_held, leaked, denied, wire_calls, secret_applied}

      status         — read VERBATIM from the receipt Cell the kernel asserted (the
                       harness never rewrites or invents a status), or "DENIED" when
                       the invoke was refused before any receipt existed;
      receipt        — the receipt cell id (provenance), or None on a pre-receipt denial;
      gate_held      — True iff the gate's own provenance is coherent: a non-revoked
                       fault produced EXACTLY one ALLOW Cell, landed BEFORE exactly
                       one socket call, with no DENY; a revoked-mid-flight call
                       produced a DENY, no ALLOW, and the socket NEVER ran;
      leaked         — True iff the raw secret appears anywhere in recorded history;
      denied         — True iff the GATE refused (a DENY Cell landed, or the invoke
                       was refused pre-receipt) — an engine failure is not a denial;
      secret_applied — True iff every socket call carried the credential (proving the
                       no-leak claim is about a secret that really rode the request).

    `args` may carry `secret`: it is POPPED before the invoke (the broker idiom —
    applied inside the engine, never on the INVOKE event) so it cannot land on the
    Weft as recorded args. REVOKED_MID_FLIGHT requires the cap to be approved at
    entry; the scenario itself performs the revocation (approve → revoke → call),
    which is PERMANENT (append-only history). `_handler` replaces the ENGINE (the
    mutation seam the regression check uses) — never the gate, never the verdict."""
    if fault not in FAULTS:
        raise LiveWorldError(f"unknown fault {fault!r} (known: {', '.join(FAULTS)})")
    args = dict(args or {})
    secret = str(args.pop("secret", ""))       # applied INSIDE the probe, never recorded

    cap = k.weave().get(cap_id)
    if cap is None or getattr(cap, "type", None) != "capability":
        raise LiveWorldError(f"{cap_id!r} is not an egress capability cell")
    hosts = sorted(cap.content.get("caveats", {}).get("egress_allowlist", []))
    if not hosts:
        raise LiveWorldError("the egress capability carries no allowlist "
                             "(deny-by-default: nothing to probe)")
    url = f"https://{hosts[0]}/liveworld/probe"    # deterministic: first allowlisted host

    def allow_now():
        return _decision_counts(k, cap_id)[0]

    before_allow, before_deny = _decision_counts(k, cap_id)
    calls = []
    open_fn = fault_open(fault, secret=secret, calls=calls, witness=allow_now)

    # Construct the gated transport while the grant is LIVE (Morta-approved); for the
    # revoked-mid-flight fault the revocation lands AFTER construction and BEFORE the
    # call — the per-call rule of egress must still refuse at the wire (fail closed).
    if fault == REVOKED_MID_FLIGHT and cap_id not in k.approvals:
        raise LiveWorldError("REVOKED_MID_FLIGHT means approved-then-revoked: approve "
                             "the egress capability before running this scenario")
    transport = live_wire.gated_transport(k, agent_cell, cap_id, _open=open_fn)
    if fault == REVOKED_MID_FLIGHT:
        k.revoke(cap_id)                       # Morta RETRACT — mid-flight, permanent

    # Register the hermetic probe effect + capability via the PUBLIC integration path
    # (executor.register + a granted capability; registration confers no authority —
    # the wire still runs the full rule of egress inside the handler's call).
    make = _handler or probe_handler
    probe_cap = k.integrate_tool(PROBE_EFFECT, make(transport, url, secret),
                                 caveats={"effect_class": "COMMUNICATION"})
    if getattr(agent_cell, "id", None) != k.decima_agent_id:
        k.grant(probe_cap, agent_cell.id)
    acting = k.weave().get(agent_cell.id)      # envelope state advanced on the Weave

    res = k.invoke(acting, probe_cap, args)

    rid = res.get("result_cell")
    receipt_cell = k.weave().get(rid) if rid else None
    # The status is the RECEIPT's status, verbatim — never the harness's invention.
    status = receipt_cell.content.get("status") if receipt_cell is not None else "DENIED"

    after_allow, after_deny = _decision_counts(k, cap_id)
    new_allow, new_deny = after_allow - before_allow, after_deny - before_deny
    fired = len(calls)
    if fault == REVOKED_MID_FLIGHT:
        # Fail closed: the DENY is recorded, no ALLOW, and the socket never ran.
        gate_held = (fired == 0 and new_deny >= 1 and new_allow == 0)
    else:
        # Exactly one ALLOW Cell, landed BEFORE exactly one socket call, no DENY.
        gate_held = (fired == 1 and new_allow == 1 and new_deny == 0
                     and all(c["allows_at_socket"] == before_allow + 1 for c in calls))

    return {
        "fault": fault,
        "status": status,
        "receipt": rid,
        "gate_held": bool(gate_held),
        "leaked": secret_on_weft(k, secret),
        # a DENIAL is the GATE refusing (its own provenance says so) — an engine
        # failure is an outcome, not a denial.
        "denied": bool(new_deny > 0 or (rid is None and "denied" in res)),
        "wire_calls": fired,
        "secret_applied": bool(calls) and all(c["auth_applied"] for c in calls),
    }


# ── the verdict + the battery ──────────────────────────────────────────────────
def honest(fault, outcome: dict) -> bool:
    """The load-bearing VERDICT on one scenario outcome: did the system degrade
    HONESTLY under this fault?

    Honest requires the gate to have held AND nothing to have leaked AND — for every
    INJECTED fault — a truthful non-success status (FAILED, UNKNOWN, or a refusal).
    The healthy control (NONE) may — must be allowed to — succeed, which is what
    keeps this verdict falsifiable rather than trivially pessimistic. The harness
    never rewrites a status: this function only JUDGES the receipt the kernel
    recorded, so an engine that lies SUCCEEDED under a timeout is classified
    FABRICATED — never repaired, never excused."""
    if outcome.get("leaked") or not outcome.get("gate_held"):
        return False
    if fault == NONE:
        return True                            # a healthy probe may truthfully succeed
    return outcome.get("status") != executor.SUCCEEDED   # a fault must NEVER read SUCCEEDED


def run_suite(k, agent_cell, cap_id, *, secret: str = "", _handler=None) -> dict:
    """Run the FULL fault battery over the gated live path and return the int-valued
    report {scenarios, honest, fabricated, denials}:

      scenarios  — battery size (the healthy control + every injected fault);
      honest     — scenarios that degraded honestly per `honest(...)`;
      fabricated — scenarios that did NOT: a SUCCEEDED receipt under an injected
                   fault, a leak, or a gate breach. EXPECTED 0 — any rise is a
                   caught regression (the whole point of the harness);
      denials    — scenarios the GATE refused (fail closed), per its own DENY
                   provenance — expected exactly 1 (revoked-mid-flight), and the
                   count survives a lying engine because it folds from the wire's
                   Cells, not from the engine's claims.

    REVOKED_MID_FLIGHT runs LAST: Morta's RETRACT is permanent on an append-only
    history, so the suite CONSUMES the grant — a fresh battery needs a fresh grant.
    All counts are ints (ints-not-floats). `_handler` (the mutation seam) replaces
    the ENGINE under test, never the gate and never the verdict."""
    report = {"scenarios": 0, "honest": 0, "fabricated": 0, "denials": 0}
    for fault in FAULTS:
        out = scenario(k, agent_cell, cap_id, fault,
                       {"secret": secret} if secret else {}, _handler=_handler)
        report["scenarios"] += 1
        report["denials"] += 1 if out["denied"] else 0
        if honest(fault, out):
            report["honest"] += 1
        else:
            report["fabricated"] += 1
    return report
