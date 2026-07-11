"""Every required screen exists, references real API endpoints, and is wired into the app."""

from __future__ import annotations

import re

import pytest

from decima.services.api import routes
from tests.shell.conftest import FRONTEND, SCREENS_DIR

# The nine required screens (handoff §9) → their screen module file.
REQUIRED_SCREENS = {
    "conversation": "conversation.js",
    "today": "today.js",
    "projects": "projects.js",
    "knowledge": "knowledge.js",
    "plans": "plans.js",
    "approvals": "approvals.js",
    "capabilities": "capabilities.js",
    "activity": "activity.js",
    "settings": "settings.js",
}

# The real backend paths declared in the route table.
REAL_PATHS = {r.path for r in routes.ROUTES}


@pytest.mark.parametrize("screen_id,filename", sorted(REQUIRED_SCREENS.items()))
def test_screen_file_exists(screen_id, filename):
    path = SCREENS_DIR / filename
    assert path.is_file(), f"missing screen file {filename}"
    src = path.read_text(encoding="utf-8")
    assert f'id: "{screen_id}"' in src, f"{filename} must register id {screen_id!r}"


@pytest.mark.parametrize("screen_id,filename", sorted(REQUIRED_SCREENS.items()))
def test_screen_references_real_endpoints(screen_id, filename):
    src = (SCREENS_DIR / filename).read_text(encoding="utf-8")
    # Extract the endpoints declared for this screen and confirm each names a real path.
    refs = re.findall(r"/api/v1/[A-Za-z0-9_/]+", src)
    assert refs, f"{filename} references no API endpoint"
    for ref in refs:
        assert ref in REAL_PATHS, f"{filename} references unknown endpoint {ref}"


def test_index_loads_every_screen_script():
    index = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for filename in REQUIRED_SCREENS.values():
        assert f"/js/screens/{filename}" in index, f"index.html must load {filename}"
    # And the core modules in dependency order.
    for core in ("sanitize.js", "dom.js", "api.js", "app.js"):
        assert f"/js/{core}" in index or f"/js/screens/{core}" in index


def test_approval_actions_only_in_trusted_inbox():
    # The approve/deny endpoints must be invoked from the approvals screen (trusted) only.
    approvals_src = (SCREENS_DIR / "approvals.js").read_text(encoding="utf-8")
    assert "approveInvocation" in approvals_src
    assert "denyInvocation" in approvals_src
    for other in REQUIRED_SCREENS.values():
        if other == "approvals.js":
            continue
        src = (SCREENS_DIR / other).read_text(encoding="utf-8")
        assert "approveInvocation" not in src, f"{other} must not host approve actions"
        assert "/api/v1/approvals/approve" not in src, other


def test_approval_inbox_discloses_required_fields():
    src = (SCREENS_DIR / "approvals.js").read_text(encoding="utf-8")
    for label in (
        "Requesting agent",
        "Effect",
        "Exact target",
        "Arguments",
        "Data leaving machine",
        "Provider",
        "Max cost",
        "Expiry",
        "Reversibility",
        "Causal step",
        "Reason",
    ):
        assert label in src, f"approval card missing disclosure: {label}"
    for action in ("Deny", "Approve once", "Approve with stricter limits"):
        assert action in src, f"approval card missing action: {action}"


def test_no_always_allow_control():
    # Invariant 5: there must be no "always allow everything from this agent" affordance.
    for filename in REQUIRED_SCREENS.values():
        src = (SCREENS_DIR / filename).read_text(encoding="utf-8").lower()
        assert "always allow" not in src, filename
