"""ACCESS1 — accessibility audit + output-shaping projections (a11y as data).

A sibling of REVIEW1. This check proves:
  - auditing doc/UI content with a11y issues raises `a11y_finding` Cells with the right
    severities, each carrying a `found_in` provenance edge to the content Cell (mirroring
    REVIEW1's finding shape) — and the audited content is stored as DATA, never executed;
  - shape() derives a deterministic screen-reader / alt-text / captions PROJECTION of the
    content (the projection-layer law) without mutating the source;
  - a CLEAN doc yields NO findings and a high (100) a11y score;
  - everything is deterministic (audit/shape are pure reads/transforms).

Contract: run(k, line). Fail loud.
"""
from decima import access


def run(k, line):
    line("\n== ACCESSIBILITY (a11y audit + output-shaping projections) — ACCESS1 ==")

    # 1. A UI doc with a spread of a11y issues. This is DATA: it is only READ.
    bad = "\n".join([
        "<h0-not-a-heading>",                                    # (no real heading… but see below)
        '<img src="/assets/hero-banner.png">',                   # missing-alt-text
        '<p style="contrast: 2">welcome</p>',                    # low-contrast (int 2 < 4)
        '<input type="text" name="email">',                      # missing-label
        '<a href="/docs">click here</a>',                        # vague-link-text
        '<img src="logo.png" alt="Acme logo">',                  # OK: has alt
    ])
    findings = access.audit(k, "signup.html", bad, author=k.reckoner.id)
    w = k.weave()
    cells = [w.get(f) for f in findings]
    line(f"  audited signup.html → {len(findings)} findings: " +
         ", ".join(sorted(f"{c.content['rule']}@L{c.content['locus']}({c.content['severity']})"
                          for c in cells)))

    # Every expected rule fired with the right severity.
    expect = {
        "missing-alt-text": "high",
        "low-contrast": "high",
        "missing-label": "high",
        "vague-link-text": "low",
    }
    for rule, sev in expect.items():
        hits = [c for c in cells if c.content["rule"] == rule]
        assert hits, f"expected a11y rule {rule!r} to fire on signup.html"
        assert all(c.content["severity"] == sev for c in hits), \
            f"{rule} severity != {sev}: {[c.content['severity'] for c in hits]}"

    # The OK <img alt=...> did NOT raise a missing-alt-text finding.
    alt_hits = [c for c in cells if c.content["rule"] == "missing-alt-text"]
    assert len(alt_hits) == 1, f"exactly one image lacks alt; got {len(alt_hits)}"

    # 2. Provenance: every finding has a found_in edge to the content cell (and only it).
    content_cell = access.content_id_for("signup.html")
    for c in cells:
        prov = w.edges_from(c.id, access.FOUND_IN)
        assert prov and prov[0]["dst"] == content_cell, \
            f"finding {c.content['rule']} lost provenance"
        assert c.content["source"] == content_cell
    line(f"  every finding → found_in→{content_cell[:8]} (provenance to the content cell ✓)")

    # 3. THE LAW: the audited content is stored as DATA — untrusted, instruction-ineligible.
    cc = w.get(content_cell)
    assert cc is not None and cc.type == "content"
    assert cc.content.get("instruction_eligible") is False, "audited content must be DATA"
    assert cc.content.get("trusted") is False
    assert "click here" in cc.content["body"], "content stored verbatim, as data"
    line(f"  content cell {content_cell[:8]} stored as DATA "
         f"(instruction_eligible={cc.content['instruction_eligible']} ✓)")

    # 4. OUTPUT-SHAPING (the projection-layer law): deterministic accessible projections.
    sr = access.shape(k, "signup.html", bad, mode="screen-reader", author=k.reckoner.id)
    assert "[image: hero banner]" in sr["projection"], sr["projection"]   # derived from src
    assert "[image: Acme logo]" in sr["projection"], sr["projection"]     # preserved alt
    assert "<img" not in sr["projection"] and "<a " not in sr["projection"], "tags stripped"

    alt = access.shape(k, "signup.html", bad, mode="alt-text", author=k.reckoner.id)
    assert 'alt="hero banner"' in alt["projection"], alt["projection"]    # backfilled
    assert 'alt="Acme logo"' in alt["projection"], "existing alt preserved"

    caps = access.shape(k, "signup.html", bad, mode="captions", author=k.reckoner.id)
    assert caps["projection"].startswith("WEBVTT"), caps["projection"]

    # The projection is a derived Cell with provenance to the source, source unmutated.
    w = k.weave()
    src_cell = w.get(sr["source"])
    assert src_cell.content.get("shaped") is not True, "source must not be shaped/mutated"
    pc = w.get(sr["cell"])
    assert pc is not None and pc.content.get("shaped") is True
    prov = w.edges_from(pc.id, access.FOUND_IN)
    assert prov and prov[0]["dst"] == sr["source"], "projection lost provenance to source"
    line(f"  shaped signup.html → screen-reader/alt-text/captions projections "
         f"(derived cell {sr['cell'][:8]} → found_in→source ✓)")

    # 5. DETERMINISM: same (content, mode) → identical projection bytes; audit is stable.
    sr2 = access.shape(k, "signup.html", bad, mode="screen-reader", author=k.reckoner.id)
    assert sr2["projection"] == sr["projection"], "shaping must be deterministic"
    f2 = access.audit(k, "signup.html", bad, author=k.reckoner.id)
    assert sorted(f2) == sorted(findings), "audit must be deterministic (same finding ids)"
    line("  re-shape & re-audit → identical bytes/finding-ids (deterministic ✓)")

    # 6. A CLEAN doc yields NO findings, an empty summary, and a perfect (100) score.
    clean = "\n".join([
        "<h1>Welcome</h1>",
        '<img src="logo.png" alt="Acme logo">',
        '<label for="email">Email</label>',
        '<input id="email" type="text" aria-label="email address">',
        '<a href="/docs">Read the full documentation</a>',
        '<p style="contrast: 7">High-contrast body text.</p>',
    ])
    clean_findings = access.audit(k, "clean.html", clean, author=k.reckoner.id)
    assert clean_findings == [], [k.weave().get(f).content for f in clean_findings]
    assert access.summary(k, "clean.html") == {}, "a clean doc must have no findings"
    clean_score = access.score(k, "clean.html", clean, author=k.reckoner.id)
    bad_score = access.score(k, "signup.html", bad, author=k.reckoner.id)
    assert clean_score == 100, f"clean doc must score 100, got {clean_score}"
    assert isinstance(clean_score, int) and isinstance(bad_score, int), "scores are ints"
    assert bad_score < clean_score, f"buggy doc must score below clean: {bad_score} !< {clean_score}"
    line(f"  clean.html → 0 findings, score={clean_score}; signup.html score={bad_score} "
         f"(clean is silent & perfect ✓)")

    line("  → accessibility: content audited as DATA raises provenance-bearing a11y "
         "findings; output-shaping derives deterministic accessible projections.")
