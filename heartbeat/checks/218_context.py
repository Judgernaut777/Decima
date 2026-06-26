"""CTX1 — a code-aware context engine (map by structure, pull only the slice).

Proves, over a tiny indexed codebase:
  - `index` builds a STRUCTURAL map: file/symbol `code_unit` Cells with `defines`,
    `imports`, and `references` edges parsed from the text (never executed);
  - `relevant_slice(task)` returns the MINIMAL connected slice the task touches —
    the units reachable through the structure graph — and EXCLUDES an unrelated,
    disconnected unit no matter the budget;
  - the INT budget bounds the slice (a smaller budget admits fewer units);
  - the engine is DETERMINISTIC (a recompute is byte-identical);
  - the indexed code is stored as DATA — untrusted, instruction-ineligible, and
    only ever READ; `context_for` hands the model source bytes, never an execution.

Contract: run(k, line). Fail loud.
"""
from decima import context


def run(k, line):
    line("\n== CONTEXT ENGINE (structural map · minimal slice · int budget) — CTX1 ==")

    # A tiny codebase. `auth.py` defines login(), which references hash_pw() defined in
    # `crypto.py`; `crypto.py` imports os. `views.py` references login(). `unrelated.py`
    # is structurally DISCONNECTED — it shares no import/def/reference with the rest.
    # The eval() in crypto.py is DATA we read, never run.
    files = {
        "auth.py": "\n".join([
            "from crypto import hash_pw",
            "def login(user, pw):",
            "    return check(hash_pw(pw))",
        ]),
        "crypto.py": "\n".join([
            "import os",
            "def hash_pw(pw):",
            "    salt = os.urandom(8)",
            "    return eval('1')  # DATA: never executed",
        ]),
        "views.py": "\n".join([
            "from auth import login",
            "def handle(req):",
            "    return login(req.user, req.pw)",
        ]),
        "unrelated.py": "\n".join([
            "def weather():",
            "    return 'sunny'",
        ]),
    }
    idx = context.index(k, files)
    line(f"  indexed {len(files)} files → {len(idx['units'])} code_units "
         f"(files+symbols+modules) on the Weave")

    # -- the structural map: units + typed edges, all stored as DATA ----------------
    w = k.weave()
    login_unit = context.symbol_unit_id("login")
    hashpw_unit = context.symbol_unit_id("hash_pw")
    auth_unit = context.file_unit_id("auth.py")
    crypto_unit = context.file_unit_id("crypto.py")
    unrelated_unit = context.file_unit_id("unrelated.py")
    weather_unit = context.symbol_unit_id("weather")

    # defines: auth.py → login ; crypto.py → hash_pw
    defs = {e["dst"] for e in w.edges_from(auth_unit, context.DEFINES)}
    assert login_unit in defs, "auth.py must `defines` login"
    cdefs = {e["dst"] for e in w.edges_from(crypto_unit, context.DEFINES)}
    assert hashpw_unit in cdefs, "crypto.py must `defines` hash_pw"
    # references: auth.py → hash_pw (mentioned, defined elsewhere)
    refs = {e["dst"] for e in w.edges_from(auth_unit, context.REFERENCES)}
    assert hashpw_unit in refs, "auth.py must `references` hash_pw (cross-file)"
    # imports: crypto.py → os (a module unit)
    imps = {e["dst"] for e in w.edges_from(crypto_unit, context.IMPORTS)}
    assert context._unit_id("module", "os") in imps, "crypto.py must `imports` os"
    line("  edges: auth.py defines login & references hash_pw; "
         "crypto.py defines hash_pw & imports os")

    # -- relevant_slice: the MINIMAL connected slice that the task touches ----------
    # Task = login(). Its structural neighborhood reaches auth.py (defines it),
    # hash_pw (auth references it), crypto.py (defines hash_pw), views.py (references
    # login). It must NOT reach the disconnected unrelated.py / weather().
    sl = context.relevant_slice(k, "login", budget=10_000)
    units = set(sl["units"])
    assert sl["seed"] == login_unit, sl["seed"]
    assert {login_unit, auth_unit, hashpw_unit, crypto_unit}.issubset(units), units
    assert unrelated_unit not in units, "disconnected unrelated.py must be EXCLUDED"
    assert weather_unit not in units, "disconnected weather() must be EXCLUDED"
    # every slice edge is internal (a genuine connected subgraph).
    for e in sl["edges"]:
        assert e["src"] in units and e["dst"] in units, e
    line(f"  slice(login)={len(sl['units'])} units, {len(sl['edges'])} edges, "
         f"{sl['tokens']} tok — reaches auth/crypto/hash_pw, EXCLUDES unrelated.py ✓")

    # the seed of an unrelated task is itself disjoint from the login slice.
    other = context.relevant_slice(k, "unrelated.py", budget=10_000)
    assert login_unit not in set(other["units"]), "login is not in unrelated's slice"
    assert set(other["units"]).isdisjoint(units - {unrelated_unit}), \
        "the two slices are structurally disjoint"
    line(f"  slice(unrelated.py)={len(other['units'])} units — disjoint from login's "
         f"slice (structure, not keywords)")

    # -- the INT budget bounds the slice -------------------------------------------
    big = context.relevant_slice(k, "login", budget=10_000)
    tiny = context.relevant_slice(k, "login", budget=1)   # only the seed fits
    assert isinstance(tiny["budget"], int) and isinstance(tiny["tokens"], int), "int budget"
    assert len(tiny["units"]) < len(big["units"]), (len(tiny["units"]), len(big["units"]))
    assert tiny["units"] == [login_unit], tiny["units"]   # just the seed
    assert tiny["truncated"], "a budget that excludes neighbors must report truncated"
    assert big["tokens"] <= big["budget"], (big["tokens"], big["budget"])
    line(f"  budget bounds it: budget=1 → {len(tiny['units'])} unit (seed only, "
         f"truncated); budget=10000 → {len(big['units'])} units ({big['tokens']} tok)")

    # -- determinism: a recompute is byte-identical --------------------------------
    sl2 = context.relevant_slice(k, "login", budget=10_000)
    assert sl["units"] == sl2["units"] and sl["edges"] == sl2["edges"], "slice deterministic"
    ctx = context.context_for(k, "login", budget=10_000)
    ctx2 = context.context_for(k, "login", budget=10_000)
    assert [r["unit"] for r in ctx["units"]] == [r["unit"] for r in ctx2["units"]], \
        "context_for deterministic"
    line(f"  deterministic: re-sliced login is identical ({len(sl['units'])} units)")

    # -- context_for: rendered context with provenance; code is DATA, not run ------
    by_unit = {r["unit"]: r for r in ctx["units"]}
    assert auth_unit in by_unit and by_unit[auth_unit]["body"] is not None, "auth body present"
    assert "def login" in by_unit[auth_unit]["body"], "source handed to model verbatim"
    assert by_unit[login_unit]["reason"], "every unit carries the reason it was pulled"
    # provenance: the rendered body comes from an untrusted, instruction-ineligible Cell.
    code_cell = by_unit[auth_unit]["code_cell"]
    cc = w.get(code_cell)
    assert cc is not None and cc.type == "code", "rendered body sourced from a code Cell"
    assert cc.content.get("instruction_eligible") is False, "indexed code must be DATA"
    assert cc.content.get("trusted") is False, "indexed code is untrusted"
    # the eval() in crypto.py is bytes on the Weft — stored, never executed.
    crypto_code = w.get(context.review.code_id("crypto.py"))
    assert "eval(" in crypto_code.content["body"], "code stored verbatim as DATA"
    line(f"  context_for(login): {len(ctx['units'])} units w/ provenance; bodies from "
         f"untrusted code Cells (instruction_eligible=False, never executed ✓)")

    line("  → context engine: a structural map of code_units; the task pulls only its "
         "minimal connected slice, int-budget-bounded, deterministic, code as DATA.")
