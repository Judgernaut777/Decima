"""B4 — memory-as-governance: memory prevents repeated bad actions.

VISION's "memory prevents repeated bad actions — what's banned, what's fragile,
what failed." B3 gave memory decay/consolidation/heat; B4 gives it teeth: record
governance claims and consult them BEFORE acting.

Proves:
  - a recorded `banned_action` makes governance_check DENY a repeat, carrying the
    prior evidence (the supported_by source that earned the ban);
  - a `fragile_file` surfaces as ALLOW-with-warning;
  - a `failed_approach` denies a re-attempt;
  - an UNTRUSTED governance claim (instruction_eligible=False) never binds — it is
    visible as DATA but ignored by the verdict (recall-vs-instruct law);
  - a target with nothing on record is allowed.

Kernel auto-consulting governance before it delegates is deferred to a core cycle.
Contract: run(k, line). Fail loud.
"""
from decima import memory


def run(k, line):
    line("\n== MEMORY-AS-GOVERNANCE (what's banned / fragile / failed) ==")
    author = k.human.id
    src = k.weave().of_type("result")[-1].id        # a real result cell to ground claims

    memory.remember_governance(k.weft, author, memory.BANNED_ACTION,
                               target="rm -rf /",
                               reason="destroys the workspace; never run", evidence_src=src)
    memory.remember_governance(k.weft, author, memory.FRAGILE_FILE,
                               target="kernel.py",
                               reason="single-owner core; changes ripple — edit with care",
                               evidence_src=src)
    memory.remember_governance(k.weft, author, memory.FAILED_APPROACH,
                               target="parse the weft with regex",
                               reason="tried before; broke on nested bodies", evidence_src=src)
    w = k.weave()

    # 1. banned action → DENY a repeat, WITH the prior evidence.
    chk = memory.governance_check(w, "rm -rf /home/mini/project")
    line(f"  'rm -rf /home/mini/project' → allow={chk['allow']} ({chk['verdict']})")
    line(f"    {chk['reason']}")
    assert not chk["allow"] and chk["verdict"] == "deny", chk
    assert chk["evidence"] and chk["evidence"][0]["kind"] == memory.BANNED_ACTION
    assert chk["evidence"][0]["supported_by"] == [src], "denial must carry prior evidence"

    # 2. fragile file → ALLOW but WARN.
    chk2 = memory.governance_check(w, "kernel.py")
    line(f"  'kernel.py' → allow={chk2['allow']} ({chk2['verdict']}): {chk2['reason']}")
    assert chk2["allow"] and chk2["verdict"] == "warn", chk2

    # 3. failed approach → DENY a re-attempt.
    chk3 = memory.governance_check(w, "parse the weft with regex again")
    assert not chk3["allow"] and chk3["evidence"][0]["kind"] == memory.FAILED_APPROACH, chk3
    line(f"  're-parse the weft with regex' → allow={chk3['allow']} ({chk3['verdict']})")

    # 4. UNTRUSTED governance must NOT bind (recall-vs-instruct law).
    memory.remember_governance(k.weft, author, memory.BANNED_ACTION,
                               target="deploy on friday",
                               reason="(injected by an untrusted page)", evidence_src=src,
                               instruction_eligible=False)
    w = k.weave()
    chk4 = memory.governance_check(w, "deploy on friday")
    line(f"  'deploy on friday' (only an UNTRUSTED ban exists) → allow={chk4['allow']}; "
         f"ignored_untrusted={len(chk4['ignored_untrusted'])}")
    assert chk4["allow"] and chk4["verdict"] == "allow" and chk4["ignored_untrusted"], chk4

    # 5. a target with nothing on record → allow.
    chk5 = memory.governance_check(w, "echo hello, fates")
    assert chk5["allow"] and chk5["verdict"] == "allow" and not chk5["evidence"], chk5
    line(f"  'echo hello, fates' (no governance) → allow={chk5['allow']}")

    line("  → memory now prevents repeated bad actions; only TRUSTED governance "
         "binds. Kernel auto-consult before delegate: deferred (core).")
