"""TOOL-DISCOVERY, made INCREDIBLY POWERFUL — the plug-in-or-forge front door.

The built-in "find a tool that fits what you want to do" RESEARCH function is
load-bearing: given a natural-language intent, `discovery.search`/`discover` must
reliably surface the RIGHT capability from the manifest catalog before Nona ever
forges a new organ. This check registers a REALISTIC catalog (the ~24 bundled engines
plus the banking / ride / crm / ticketing rails) and proves the upgraded ranking:

  - EXACT-INTENT: several crisp intents each rank the CORRECT engine #1 with a strong,
    positive INT score (charge a card → stripe_rail, run payroll → payroll, …);
  - SYNONYM/alias: intents whose SURFACE words never appear in the target manifest still
    rank it #1 via the alias map ("hail a taxi" → ride, "wire funds …" → payouts,
    "sign a contract" → esign, "invest in some stock" → brokerage_engine);
  - FIELD WEIGHTING: a name/tag hit outranks a description-only hit;
  - AMBIGUOUS: a genuinely two-sided intent ("buy or sell shares") ranks BOTH plausible
    candidates (brokerage_engine, exchange) at the top, in a sensible order;
  - THRESHOLD: an irrelevant intent returns NOTHING above a sane threshold — `discover`
    forges instead of forcing a bad match;
  - DETERMINISM: ranking + scores are byte-identical across repeated runs; scores are
    ints (no float ever enters a score).

Contract: run(k, line). Fail loud (assert). Owns a fresh, offline Kernel.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima import manifest as M
from decima import discovery as D
from decima import builtin_manifests as B
from decima import banking, ride, crm_engine, ticketing


def run(k, line):
    line("\n== TOOL-DISCOVERY, made powerful — natural-language intent → the right tool ==")
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)

    # A REALISTIC catalog: the bundled engines + several real rails. ──────────────────
    B.register_builtins(kk)
    banking.register_manifest(kk)
    ride.register_manifest(kk)
    crm_engine.register_manifest(kk)
    ticketing.register_manifest(kk)
    n = len({c.content["name"] for c in M.registry(kk)})
    assert n >= 24, f"expected a rich catalog (>= 24 engines), got {n}"
    line(f"  registered a realistic catalog of {n} capabilities (builtins + "
         "banking/ride/crm/ticketing) ✓")

    def top(goal, k_=5):
        return D.search(kk, goal, top_k=k_)

    def assert_first(goal, expected, floor=1):
        r = top(goal)
        assert r, f"search({goal!r}) returned nothing"
        assert r[0]["name"] == expected, \
            f"search({goal!r}) → {[(x['name'], x['score']) for x in r[:4]]}; want {expected} #1"
        assert isinstance(r[0]["score"], int) and not isinstance(r[0]["score"], bool), r[0]
        assert r[0]["score"] >= floor, f"search({goal!r}) top score {r[0]['score']} < {floor}"
        return r

    # 1. EXACT-INTENT — crisp goals each rank the correct engine #1. ──────────────────
    exact = [
        ("charge a customer's credit card", "stripe_rail"),
        ("get the weather forecast", "weather_engine"),
        ("verify someone's identity", "kyc"),
        ("run payroll for the employees", "payroll"),
        ("file a support ticket in the helpdesk", "ticketing"),
        ("fetch my bank account balance", "banking"),
        ("create a new sales contact", "crm"),
    ]
    for goal, expected in exact:
        r = assert_first(goal, expected, floor=300)
        line(f"  exact  {goal!r:44} → {r[0]['name']} (score={r[0]['score']}) #1 ✓")

    # 2. SYNONYM/alias — surface words absent from the manifest, matched via the alias map.
    synonyms = [
        ("hail a taxi", "ride"),                 # taxi/hail → ride/transport
        ("wire funds to a supplier", "payouts"), # wire/funds → transfer/payout/bank
        ("sign a contract", "esign"),            # sign → esign/signature
        ("invest in some stock", "brokerage_engine"),  # invest → brokerage/trade
        ("ping someone with a message", "comms"),      # ping → message/notify
    ]
    for goal, expected in synonyms:
        r = assert_first(goal, expected, floor=300)
        line(f"  synonym {goal!r:43} → {r[0]['name']} (score={r[0]['score']}) #1 ✓")

    # 3. FIELD WEIGHTING — a name/tag hit outranks a description-only hit. ─────────────
    # "insurance" is a tag on insurance_claim; the word only ever appears in that
    # engine's fields, so a name/tag match must dominate any incidental description hit.
    r = assert_first("file an insurance claim", "insurance_claim", floor=300)
    assert r[0]["score"] > (r[1]["score"] if len(r) > 1 else 0), \
        f"the field-weighted leader must clearly separate: {[(x['name'], x['score']) for x in r[:3]]}"
    line("  field weighting: name/tag hit outranks description-only hit "
         f"({r[0]['name']}={r[0]['score']} > runner-up={r[1]['score']}) ✓")

    # 4. AMBIGUOUS — both plausible candidates rank at the top, sensibly. ──────────────
    amb = top("buy or sell shares", k_=5)
    names = [x["name"] for x in amb]
    assert names[0] == "brokerage_engine", ("shares → brokerage first", names)
    assert "exchange" in names[:2], ("exchange must be the plausible runner-up", names)
    assert amb[0]["score"] >= amb[1]["score"] > 0, amb
    line(f"  ambiguous 'buy or sell shares' → {names[:2]} "
         f"(both trading rails at the top; brokerage first) ✓")

    # 5. THRESHOLD — an irrelevant intent surfaces NOTHING above the bar → forge. ──────
    junk = "photosynthesis of chloroplasts in mesophyll"
    jr = top(junk)
    assert jr[0]["score"] == 0, f"irrelevant intent must score 0, got {jr[0]}"
    forged = D.discover(kk, junk, threshold=kk.DISCOVERY_THRESHOLD)
    assert forged["action"] == "forge", forged
    # A crisp intent, by contrast, clears the same bar and is USED (not forged).
    used = D.discover(kk, "charge a customer's credit card", threshold=kk.DISCOVERY_THRESHOLD)
    assert used["action"] == "use" and used["name"] == "stripe_rail", used
    assert isinstance(used["score"], int) and used["score"] >= kk.DISCOVERY_THRESHOLD, used
    line(f"  threshold: irrelevant intent → score 0 → action=forge; a real intent "
         f"clears the bar → use {used['name']} ({used['score']}) ✓")

    # 6. DETERMINISM — byte-identical ranking + ints only across repeated runs. ────────
    for goal, _ in exact + synonyms:
        assert top(goal) == top(goal), f"search({goal!r}) must be deterministic"
        assert all(isinstance(x["score"], int) and not isinstance(x["score"], bool)
                   for x in top(goal)), f"scores must be ints: {goal!r}"
    assert D.discover(kk, junk, threshold=kk.DISCOVERY_THRESHOLD) == forged
    line("  ranking + scores byte-identical across repeated runs; ints only (no float) ✓")

    line("  → discovery is powerful: natural-language intent (incl. synonyms) reliably "
         "surfaces the right capability; ambiguity ranks the plausible set; irrelevance "
         "forges — deterministic, pure-stdlib, zero-LLM, integer scoring.")
