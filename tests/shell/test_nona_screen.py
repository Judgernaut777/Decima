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
  * point the rollback button at ``revokeCapability`` and the conflation test goes red.
"""

from __future__ import annotations

import re

from decima.services.api import routes
from decima.services.nona import promotion
from tests.shell.conftest import FRONTEND, SCREENS_DIR

SCREEN = SCREENS_DIR / "nona.js"
SRC = SCREEN.read_text(encoding="utf-8")

# Every path the screen's `endpoints:` array and its api.js wrappers can name.
REAL_PATHS = {r.path for r in routes.ROUTES}


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
