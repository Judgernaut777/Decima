"""The self-extension screen renders attacker-authored source safely and labels tiers honestly.

The Nona screen is the one surface in the Shell whose CONTENT is hostile by construction:
an extension candidate's implementation is generated code written by a model (or by whoever
proposed the candidate), it is quarantined DATA on the log, and this screen's whole purpose
is to show it to a human before anything is allowed to run it. These are static assertions
over the shipped screen module, so the discipline is guarded by the ordinary pytest gate;
tests/browser/specs/nona.spec.js then drives the real rendered surface and proves the bytes
stayed inert in a real browser.

Each test here would fail if the property broke:

  * remove the untrusted zone (or render the source through anything but ``el(… text …)``)
    and the rendering test goes red;
  * reimplement the tier→signer mapping in JS and the honesty test goes red, because the
    screen would then be able to disagree with ``promotion.signer_policy``;
  * point the rollback button at ``revokeCapability`` and the conflation test goes red;
  * read a promotion-record field the reader never sends — the shape of the bug this file's
    last test was added for — and the promotion-pill test goes red, because that field set
    is DERIVED from a real rolled-back promotion rather than restated here.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Any

from decima.kernel import model
from decima.kernel.crypto import Keyring
from decima.kernel.weave import Weave
from decima.kernel.weft import Weft
from decima.services.api import routes
from decima.services.nona import anchors, promotion, reckoner
from decima.services.nona.reckoner import Metrics
from tests.shell.conftest import FRONTEND, SCREENS_DIR

SCREEN = SCREENS_DIR / "nona.js"
SRC = SCREEN.read_text(encoding="utf-8")

# Every path the screen's `endpoints:` array and its api.js wrappers can name.
REAL_PATHS = {r.path for r in routes.ROUTES}

_CAP = "cap:organ"
_CANDIDATE = "candidate:organ"
_CONTAINED = {
    "no_new_privs": True,
    "network_denied": True,
    "chroot": True,
    "namespaces": True,
    "matrix_version": 1,
}


def _rolled_back_promotion_record() -> dict[str, Any]:
    """One REAL promotion record, rolled back, exactly as the reader hands it to the screen.

    Built by promoting and then retracting on a real Weft rather than by restating the
    field names here: a test that hardcodes the contract cannot notice the contract moving,
    and the defect this guards against was precisely the screen and the reader disagreeing
    about a field name. ``nona_service.get_candidate`` passes this list through verbatim as
    ``body["promotions"]``, which is what ``renderDetail`` iterates.
    """
    kr = Keyring(seed=bytes(32))
    weft = Weft(os.path.join(tempfile.mkdtemp(), "weft.db"), kr)
    root = kr.mint("root", "root").id
    reck = kr.mint(anchors.RECKONER_NAME, "reckoner").id
    anchors.install_trust_anchors(weft, root, reckoner=reck)
    model.assert_content(
        weft,
        root,
        _CAP,
        "capability",
        {
            "effect": "generated_code",
            "declared_effect_class": anchors.PURE,
            "quarantined": True,
            "parent": None,
            "grantee": kr.mint("holder", "operator").id,
            "granter": root,
            "caveats": {"sandbox_only": True, "requires_approval": True},
        },
    )
    verdict = reckoner.gate(
        Metrics(deterministic_cases=2, deterministic_pass=2, hostile_cases=1, hostile_contained=1)
    )
    assert verdict.eligible, "the positive control must really be promote-eligible"
    evaluation = reckoner.record_result(
        weft,
        reck,
        candidate_cell=_CANDIDATE,
        suite_cell="suite:s",
        implementation_digest="blob_d",
        verdict=verdict,
        containment=dict(_CONTAINED),
    )
    out = promotion.promote(
        weft,
        Weave.fold(weft),
        reck,
        capability=_CAP,
        candidate=_CANDIDATE,
        evaluation=evaluation,
        tier=anchors.PURE,
    )
    live = promotion.promotion_state(Weave.fold(weft), _CAP)["promotions"]
    assert len(live) == 1 and live[0]["live"] is True, "positive control: it was live first"

    promotion.rollback(weft, root, out["promotion"])
    (record,) = promotion.promotion_state(Weave.fold(weft), _CAP)["promotions"]
    assert record["live"] is False, "…and the rolled-back record is the one under test"
    return record


def test_screen_is_registered_and_loaded():
    assert 'id: "nona"' in SRC, "the screen must register the id the nav + specs use"
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert "/js/screens/nona.js" in index, "index.html must load the screen module"


def test_every_endpoint_the_screen_names_is_a_real_route():
    refs = re.findall(r"/api/v1/[A-Za-z0-9_/]+", SRC)
    assert refs, "the screen declares no endpoints"
    for ref in refs:
        assert ref in REAL_PATHS, f"nona.js references unknown endpoint {ref}"
    # And it names the four nona routes a promote/rollback surface cannot work without.
    for path in (
        "/api/v1/nona/candidates",
        "/api/v1/nona/candidates/detail",
        "/api/v1/nona/promote",
        "/api/v1/nona/rollback",
    ):
        assert path in SRC, f"the screen must declare {path}"


def test_generated_source_is_rendered_as_text_inside_an_untrusted_zone():
    """The source may reach the DOM only as a text node in a labelled untrusted zone."""
    # The zone exists, is of the untrusted kind, and its body is a <pre> fed via `text`.
    assert 'D.dom.zone("untrusted"' in SRC, "generated source needs an untrusted zone"
    assert re.search(r'el\("pre",\s*\{\s*class:\s*"nona-source",\s*text:\s*data\.source', SRC), (
        "the source <pre> must receive the bytes via dom.el's `text` (textContent), not markup"
    )
    # No markup sink (test_no_forbidden_patterns greps every script for these too; the
    # duplication is deliberate — this is the one screen where the content is hostile by
    # construction, so the assertion belongs next to the reason for it).
    assert not re.search(r"\.(inner|outer)HTML\s*=", SRC), "nona.js must not assign HTML"
    for sink in ("insertAdjacentHTML", "document.write(", "new Function("):
        assert sink not in SRC, f"nona.js must not use {sink}"
    # No second escaping scheme, and no interpolation of candidate content into markup.
    assert "escapeHtml" not in SRC, (
        "escaping is dom.js's job (textContent); a screen-local escaper would be a second, "
        "divergent scheme for the same problem"
    )


def test_the_tier_label_is_not_reimplemented_in_the_screen():
    """The screen may translate a signer-policy TOKEN into a sentence; it may not decide it.

    If the mapping lived here it could disagree with what the kernel gates on the moment a
    tier moved, so the tier names themselves must not appear as keys of a policy table.
    """
    assert "signer_policy" in SRC, "the screen must read the backend's signer_policy"
    for tier in ("pure", "read_only", "workspace_write", "financial"):
        assert f"{tier}:" not in SRC, (
            f"nona.js keys something by tier {tier!r} — the tier→policy decision belongs to "
            "promotion.SIGNER_POLICY, and duplicating it lets the label drift from the gate"
        )
    # The three policy tokens it does translate are exactly the backend's vocabulary.
    for token in (promotion.AUTOMATED, promotion.HUMAN, promotion.NOT_EXECUTABLE):
        assert f"{token}:" in SRC, f"the screen does not label the {token!r} policy"


def test_a_tier_with_no_executor_is_labelled_not_executable_not_approvable():
    """Design Decision 2: never offer an approval for something that cannot run."""
    assert "NOT EXECUTABLE" in SRC, "the not_executable policy must say so in words"
    assert "no mediated egress" in SRC, "…and say why (there is no networked executor)"
    # The promote button is offered only when the backend says something can run it.
    assert "if (c.executable && c.eligible_evaluation)" in SRC, (
        "promote must be gated on the backend's `executable` flag and eligible evidence; "
        "otherwise a network-tier organ gets a button that no approval could ever satisfy"
    )


def test_rollback_is_never_wired_to_revocation():
    """Demotion and revocation are different consequences and must stay different buttons."""
    assert "rollbackPromotion" in SRC
    assert "revokeCapability" not in SRC, (
        "the rollback control must call RollbackPromotion; RevokeCapability cascades "
        "DERIVED_AUTHORITY and destroys authority the operator did not intend to destroy"
    )
    assert "does not revoke the capability" in SRC, (
        "the screen must say plainly that a rollback is not a revocation"
    )


def test_the_promotion_record_pill_reads_a_field_the_reader_actually_emits():
    """A rolled-back promotion must not be able to render as a live one.

    The screen's own claim is that the evidence is the folded facts, "so the view cannot
    disagree with enforcement". A pill keyed off a field the reader never sends breaks that
    silently and in the dangerous direction: the condition is `undefined` for every record,
    so EVERY promotion — live or withdrawn — renders identically, and the operator is told a
    withdrawn promotion is still in force on the same screen that says the organ is
    quarantined. So: every field the record renderer reads must exist on a real record, and
    the pill's two branches must agree on polarity.
    """
    record = _rolled_back_promotion_record()
    # Just the promotion-record renderer, so an unrelated `p` elsewhere cannot widen this.
    block = SRC[SRC.index("promotions.map(function (p)") : SRC.index('"nona-promotion-card"')]
    read = set(re.findall(r"\bp\.([A-Za-z_][A-Za-z0-9_]*)", block))
    assert read, "the promotion-record renderer reads no fields at all — wrong block sliced"
    unknown = read - set(record)
    assert not unknown, (
        f"nona.js reads {sorted(unknown)} off a promotion record, but promotion_state emits "
        f"only {sorted(record)}; an absent field is `undefined`, which silently pins the "
        "record's pill to one branch for live and rolled-back promotions alike"
    )
    # Polarity, both halves: the LIVE branch is the one that says live and looks ok.
    assert re.search(r'p\.live\s*\?\s*"live"\s*:\s*"rolled back"', block), (
        "the record's label must read `live` when the promotion is live, and `rolled back` "
        "when it is not"
    )
    assert re.search(r'p\.live\s*\?\s*"ok"\s*:\s*"warn"', block), (
        "…and the pill's colour must follow the same field in the same direction, or the "
        "words and the colour disagree"
    )


def test_the_screen_hosts_no_approval_control():
    """Approvals are decided in the trusted inbox; this screen only proposes."""
    assert "approveInvocation" not in SRC
    assert "/api/v1/approvals" not in SRC
    assert "Approval inbox" in SRC, "…and it must point the operator there"


def test_gated_submission_is_reported_as_pending_not_as_done():
    """A 202 is the success path for a gated command and must not read as 'applied'."""
    assert "r.status === 202" in SRC and "required_approval" in SRC
    assert "nothing has changed yet" in SRC, (
        "the toast for a deferred gated command must say the world did not move"
    )
