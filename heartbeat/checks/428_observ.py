"""OBSERVABILITY — operational metrics folded from the Weft (a lens, no new storage).

`decima/observ.py` folds ONE deterministic, int-only report (`metrics(k)`) from the SAME
public projections every other lane already exposes — `weave.invocations`, `of_type("result")`,
`jobs.JOB` status, `spend.CHARGE`, `redact.REDACTION` — composing the canary-health idiom
(`weave.canary_health`) across every invoked capability instead of just one CANARY. It adds
NO storage (Law 5): calling `metrics(k)` reads only, asserts nothing, mints no capability.

This check proves, offline + deterministically:

  (a) GROUND TRUTH — build a kernel with KNOWN activity (a granted invoke that SUCCEEDS, a
      sandboxed invoke that is denied/FAILS, a job run to done, a job recovered via
      resume.recover after a simulated crash window, a secret-sensitive redaction) and assert
      every metric in `metrics(k)` equals exactly what was actually done;
  (b) PURE PROJECTION (load-bearing) — calling `metrics(k)` (repeatedly) adds ZERO events/Cells
      to the Weave and mints no capability: `len(list(k.weft.events()))` and
      `len(k.weave().cells)` are UNCHANGED before/after;
  (c) INTS — every numeric leaf `metrics(k)` returns is a plain `int` (never a float, never a
      bool-as-int);
  (d) DETERMINISM — `metrics(k) == metrics(k)` on the same fold (repeated calls agree).

Mutation-resistance (the load-bearing line): drop the `denials += 1` line under the
`_is_failure_receipt(r)` check in `observ.metrics`, and this check's ground-truth assertion
`assert m["denials"] == 1` goes red — the one FAILED/denied sandboxed invoke this check
deliberately manufactures would silently vanish from the report.

Contract: run(k, line). Fail loud (assert). Owns fresh Kernels reconstructed over one db.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import jobs, resume, reactor, executor, spend, redact
from decima.inbox import ApprovalInbox
from decima import observ

_PROBE_OK = "observ_probe_ok"


def _kernel(db):
    return Kernel(db, fresh=True)


def _fresh():
    db = os.path.join(tempfile.mkdtemp(), "weft.db")
    return _kernel(db)


def _assert_int(x, what):
    assert isinstance(x, int) and not isinstance(x, bool), \
        f"{what} must be an int (ints-not-floats), got {type(x).__name__} {x!r}"


def _assert_ints_deep(obj, path="metrics"):
    """Walk the whole metrics() dict and assert every LEAF number is a plain int."""
    if isinstance(obj, dict):
        for k2, v in obj.items():
            _assert_ints_deep(v, f"{path}.{k2}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _assert_ints_deep(v, f"{path}[{i}]")
    elif isinstance(obj, (int, float)):
        _assert_int(obj, path)
    # strings (cap ids/names) pass through untouched


def run(k, line):
    line("\n== OBSERVABILITY — operational metrics folded from the Weft (a lens, no storage) ==")

    executor.register(_PROBE_OK, lambda impl, args: {"out": "ran:" + str(args.get("text", ""))})

    k = _fresh()

    # ── (a) GROUND TRUTH — build KNOWN activity, one deed at a time. ──────────────────

    # 1. one SUCCEEDED invoke through a plain granted capability.
    ok_cap = k._assert_cap("observ.ok", _PROBE_OK)
    k.grant(ok_cap, k.decima_agent_id)
    agent = k.weave().get(k.decima_agent_id)     # refetch: grant() re-asserts the agent Cell
    res_ok = k.invoke(agent, ok_cap, {"text": "hello"})
    assert res_ok.get("status") == "SUCCEEDED", f"ground-truth OK invoke must succeed: {res_ok}"

    # 2. one FAILED/denied invoke: a sandbox-restricted capability invoked past its scope —
    #    this DOES land an INVOKE event + a FAILED receipt (unlike an ungranted invoke, which
    #    never touches the Weft at all and so has no metric footprint to assert against).
    bad_cap = k._assert_cap("observ.blocked", _PROBE_OK,
                             caveats={"sandbox": {"effects": ["some_other_effect"]}})
    k.grant(bad_cap, k.decima_agent_id)
    agent = k.weave().get(k.decima_agent_id)     # refetch again after this grant
    res_bad = k.invoke(agent, bad_cap, {"text": "over-reach"})
    assert "denied" in res_bad and res_bad.get("status") == "FAILED", \
        f"ground-truth sandboxed invoke must be denied with a FAILED receipt: {res_bad}"

    # 3. a job run to DONE (never crashes) — the normal due-lane.
    done_cap = k._assert_cap("observ.job_ok", _PROBE_OK)
    k.grant(done_cap, k.decima_agent_id)
    jid_done = jobs.enqueue(k, "digest", capability=done_cap, run_at=0, max_uses=1, window=1000)
    tick0 = reactor.tick(k, 0)
    assert any(j["job"] == jid_done and j["status"] == jobs.DONE for j in tick0["jobs"]), \
        f"ground-truth job must run to DONE: {tick0}"

    # 4. a job recovered via resume.recover after a simulated crash window.
    rec_cap = k._assert_cap("observ.job_recover", _PROBE_OK)
    k.grant(rec_cap, k.decima_agent_id)
    jid_rec = jobs.enqueue(k, "payroll", capability=rec_cap, run_at=0, max_uses=1, window=1000)
    st = jobs.status(k, jid_rec)
    fire = k.invoke(k.weave().get(st["runner"]), st["lease"], {"text": "crash-window"})
    assert fire.get("status") == "SUCCEEDED", f"pre-crash fire must succeed: {fire}"
    assert k.weave().get(jid_rec).content["status"] == jobs.ENQUEUED, \
        "the crash window must leave the job ENQUEUED with its effect already fired"
    report = resume.recover(k, 0)
    assert {"job": jid_rec, "status": jobs.DONE} in report["reconciled"], \
        f"ground-truth job must be recovered to DONE: {report}"

    # 5. a secret-sensitive redaction.
    _scrubbed, findings = redact.scrub("api key sk-abcdefghijklmnopqrstuvwx in the task text")
    classification = redact.classify_privacy("api key sk-abcdefghijklmnopqrstuvwx", findings)
    assert classification == redact.SECRET_SENSITIVE, f"probe text must classify secret_sensitive: {classification}"
    redact.record_redaction(k, findings, classification)

    # 6. a spend charge, through the real confirm-charge/approve path (no ambient spend).
    agent = k.weave().get(k.decima_agent_id)     # refetch: prior grants re-asserted the agent
    inbox = ApprovalInbox(k)
    meter = spend.SpendMeter(k)
    spend_cap = meter.mint_spend_capability(agent, "observ-provider")
    meter.configure_budget(1_000_000)
    req = meter.request_charge(inbox, agent, spend_cap, provider_id="observ-provider",
                               tokens=1000, cost_per_1k_microcents=500, privacy_tier="external_paid",
                               now_tick=0)
    assert "queued" in req, f"ground-truth charge must queue: {req}"
    charged = meter.approve_charge(inbox, req["queued"])
    assert "charged" in charged, f"ground-truth charge must enact: {charged}"

    line("  ground truth built: 1 SUCCEEDED invoke, 1 FAILED/denied (sandboxed) invoke, 1 job "
         "run to DONE, 1 job crash-recovered to DONE, 1 secret_sensitive redaction, 1 approved "
         "spend charge (500 microcents) ✓")

    # ── snapshot the Weft/Weave BEFORE metrics() so (b) can diff after. ───────────────
    events_before = len(list(k.weft.events()))
    cells_before = len(k.weave().cells)

    m = observ.metrics(k)

    # -- assert every metric against the KNOWN ground truth --------------------------
    assert m["invocations"] >= 4, f"invocations must count every INVOKE (ok/bad/2 job fires): {m}"
    assert m["denials"] == 1, f"exactly one FAILED/denied receipt (the sandboxed invoke): {m['denials']}"
    assert m["receipts_by_status"]["SUCCEEDED"] >= 3, \
        f"3 SUCCEEDED receipts (ok invoke + 2 job fires): {m['receipts_by_status']}"
    assert m["receipts_by_status"]["FAILED"] == 1, f"1 FAILED receipt: {m['receipts_by_status']}"
    assert m["jobs"]["done"] == 2, f"2 jobs DONE (digest + the recovered payroll): {m['jobs']}"
    assert m["jobs"]["recovered"] == 1, f"exactly 1 job flagged recovered: {m['jobs']}"
    assert m["jobs"]["enqueued"] == 0, f"no job left enqueued: {m['jobs']}"
    assert m["spend_microcents"] == 500, f"spend must fold the approved charge exactly: {m}"
    assert m["redactions"]["total"] == 1 and m["redactions"]["secret_sensitive"] == 1, \
        f"exactly 1 redaction, secret_sensitive: {m['redactions']}"
    by_verb_sum = sum(m["by_verb"].values())
    assert by_verb_sum == m["events_total"], \
        f"by_verb must partition events_total exactly: {m['by_verb']} vs {m['events_total']}"
    assert m["events_total"] == events_before, \
        "events_total must equal the actual folded event count"
    per_cap = {pc["cap"]: pc for pc in m["per_capability"]}
    assert ok_cap in per_cap and per_cap[ok_cap]["invocations"] == 1 and per_cap[ok_cap]["failures"] == 0
    assert bad_cap in per_cap and per_cap[bad_cap]["invocations"] == 1 and per_cap[bad_cap]["failures"] == 1
    line(f"  ground truth EQUALS the fold: invocations={m['invocations']} denials={m['denials']} "
         f"receipts={m['receipts_by_status']} jobs={m['jobs']} spend={m['spend_microcents']} "
         f"redactions={m['redactions']} — per-capability breakdown matches (ok=1/0, blocked=1/1) ✓")

    # ── (b) PURE PROJECTION (load-bearing) — metrics() adds ZERO events/Cells. ────────
    _m_again = observ.metrics(k)
    events_after = len(list(k.weft.events()))
    cells_after = len(k.weave().cells)
    assert events_after == events_before, \
        f"metrics(k) must NOT append to the Weft — a lens, never a writer: {events_before} -> {events_after}"
    assert cells_after == cells_before, \
        f"metrics(k) must NOT mint/assert any Cell: {cells_before} -> {cells_after}"
    line(f"  pure projection: events {events_before}->{events_after}, cells {cells_before}->"
         f"{cells_after} — metrics(k) called twice mints NO capability and adds NO Cell ✓")

    # ── (c) INTS — every numeric leaf is a plain int, never a float/bool-as-int. ──────
    _assert_ints_deep(m)
    # a bool-smuggling probe: the failure-status tally must not silently accept True as 1 —
    # the door coercion (_ct) is what makes this fail loud if it were ever removed.
    assert type(m["denials"]) is int and type(m["denials"]) is not bool
    line("  ints-not-floats: every numeric leaf of metrics(k) — events, invocations, denials, "
         "per-status/effect-class tallies, job counts, spend_microcents, redaction counts, "
         "per-capability counts — is a plain int, never a float or a bool-as-int ✓")

    # ── (d) DETERMINISM — metrics(k) == metrics(k) on the same fold. ──────────────────
    m2 = observ.metrics(k)
    assert m == m2, f"metrics(k) must be deterministic on the same fold: {m} != {m2}"
    line("  determinism: metrics(k) == metrics(k) on the same fold — no wall-clock, no "
         "unseeded randomness, no arrival-order dependence ✓")

    # ── dashboard_lines() is a readable projection of the same fold. ─────────────────
    dl = observ.dashboard_lines(k)
    assert isinstance(dl, list) and all(isinstance(x, str) for x in dl) and len(dl) >= 5, \
        f"dashboard_lines must be a list of display-line strings: {dl}"
    assert any("denials=1" in x for x in dl), f"dashboard must surface the denial count: {dl}"
    line(f"  dashboard_lines: {len(dl)} human-readable lines rendered from the SAME fold "
         "(a shell 'observe' view over metrics(), never a second source of truth) ✓")

    line("  → observability is now a LENS: one deterministic, int-only report folded from the "
         "SAME Cells every other lane already signs — zero new storage, zero new authority, "
         "byte-identical on repeated calls, and every leaf provably an int.")
