"""REVIEW1 — code review as data (lint findings on the signed Weft).

A sibling of DET1: lint-style rules (regex/substring heuristics) run over a CODE Cell
that is stored as UNTRUSTED DATA. This check proves:
  - a file with issues raises `review_finding` Cells with the right severities, each
    carrying a `found_in` provenance edge back to the code Cell;
  - a CLEAN file yields no findings;
  - the reviewed code is stored as DATA — `instruction_eligible=False`, never executed.

Contract: run(k, line). Fail loud.
"""
from decima import review


def run(k, line):
    line("\n== CODE REVIEW (lint findings as data, code is never executed) — REVIEW1 ==")

    # 1. A file with a spread of issues. This is DATA: the eval()/exec() in it are
    #    text we READ, never run.
    bad = "\n".join([
        "def login(user):",                          # missing-docstring (def, no docstring)
        "    password = \"hunter2\"",                # hardcoded-secret
        "    result = eval(user_input)",             # eval-call
        "    exec(payload)",                          # exec-call
        "    # TODO: validate the user before trusting input",  # todo-comment
        "    x = " + "1 + " * 30 + "1",              # overlong-line (>100 cols)
        "    return result",
    ])
    findings = review.review(k, "auth.py", bad, author=k.reckoner.id)
    w = k.weave()
    cells = [w.get(f) for f in findings]
    rules = {c.content["rule"] for c in cells}
    line(f"  reviewed auth.py → {len(findings)} findings: " +
         ", ".join(sorted(f"{c.content['rule']}@L{c.content['line']}({c.content['severity']})"
                          for c in cells)))

    # Every expected rule fired, with the right severity.
    expect = {
        "eval-call": "high", "exec-call": "high", "hardcoded-secret": "high",
        "todo-comment": "low", "missing-docstring": "medium", "overlong-line": "low",
    }
    for rule, sev in expect.items():
        hits = [c for c in cells if c.content["rule"] == rule]
        assert hits, f"expected rule {rule!r} to fire on auth.py"
        assert all(c.content["severity"] == sev for c in hits), \
            f"{rule} severity != {sev}: {[c.content['severity'] for c in hits]}"

    # 2. Provenance: every finding has a found_in edge to the code cell (and only it).
    code_cell = review.code_id("auth.py")
    for c in cells:
        prov = w.edges_from(c.id, review.FOUND_IN)
        assert prov and prov[0]["dst"] == code_cell, f"finding {c.content['rule']} lost provenance"
        assert c.content["source"] == code_cell
    line(f"  every finding → found_in→{code_cell[:8]} (provenance to the code cell ✓)")

    # 3. THE LAW: the reviewed code is stored as DATA — untrusted, instruction-ineligible,
    #    never executed. (The eval()/exec() above are bytes on the Weft, nothing more.)
    cc = w.get(code_cell)
    assert cc is not None and cc.type == "code"
    assert cc.content.get("instruction_eligible") is False, "reviewed code must be DATA"
    assert cc.content.get("trusted") is False
    assert "eval(" in cc.content["body"], "code stored verbatim, as data"
    line(f"  code cell {code_cell[:8]} stored as DATA "
         f"(instruction_eligible={cc.content['instruction_eligible']}, never executed ✓)")

    # 4. summary() groups findings by severity for the file.
    grouped = review.summary(k, "auth.py")
    by_sev = {sev: len(cs) for sev, cs in grouped.items()}
    assert by_sev.get("high") == 3, by_sev    # eval, exec, secret
    line(f"  summary(auth.py) by severity: " +
         ", ".join(f"{s}:{n}" for s, n in sorted(by_sev.items())))

    # 5. A CLEAN file yields NO findings — and summary() is empty for it.
    clean = "\n".join([
        "def greet(name):",
        '    """Return a friendly greeting for name."""',
        "    return f'hello {name}'",
    ])
    clean_findings = review.review(k, "clean.py", clean, author=k.reckoner.id)
    assert clean_findings == [], [k.weave().get(f).content for f in clean_findings]
    assert review.summary(k, "clean.py") == {}, "a clean file must have no findings"
    line("  clean.py → 0 findings (a clean file is silent ✓)")

    line("  → code review: untrusted code stored as DATA, lint rules READ it and raise "
         "provenance-bearing findings; nothing under review is ever executed.")
