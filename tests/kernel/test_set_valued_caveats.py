"""Set-valued caveats attenuate by SUBSET — the rule that did not exist.

`_caveats_downhill` knew two shapes of caveat: numeric bounds that may only shrink, and flags
that must persist. A third shape — a caveat that ENUMERATES what is permitted rather than
bounding it — fell through to the flag rule, which only asks whether the child's value is
truthy. So a child could take a parent's `[a]` and hand on `[a, b]`, and `attenuation_valid`
approved it: authority widened on the way DOWNHILL, which is the one thing the ocap core
exists to make impossible.

Nothing in the tree was list-valued yet, and that is precisely why it went unnoticed — the
first enumerating caveat to arrive would have carried the defect in with it. `egress_allow`
(the destinations an organ may reach) is that first caveat, so the rule lands before anything
reads the value: egress has no executor, and the attenuation rule has to be right BEFORE
something acts on it, not after.

The rule keys on the parent value's SHAPE, not on a registry of names, so it closes the class
rather than the instance. Both halves are tested: the VALIDATOR must refuse a widening child,
and the CONSTRUCTOR must be unable to build one — a constructor that can produce an invalid
object and a validator that rejects it is one refactor away from a hole.
"""

from __future__ import annotations

from typing import Any

from decima.kernel import capability

ALLOW = capability.EGRESS_ALLOW


def _cap(**caveats: Any) -> dict[str, Any]:
    return {"caveats": dict(caveats)}


def _parent() -> dict[str, Any]:
    return _cap(**{ALLOW: ["api.example.com", "cdn.example.com"]})


# ── the validator ────────────────────────────────────────────────────────────
def test_a_child_may_not_widen_an_enumerated_caveat() -> None:
    """The headline. Before this rule the same two dicts returned True."""
    child = _cap(**{ALLOW: ["api.example.com", "cdn.example.com", "evil.example.com"]})
    assert capability._caveats_downhill(child, _parent()) is False


def test_an_equal_set_is_valid_and_a_narrower_one_is_too() -> None:
    """The positive controls. Without them "widening is refused" would pass on a rule that
    refused everything."""
    assert capability._caveats_downhill(
        _cap(**{ALLOW: ["api.example.com", "cdn.example.com"]}), _parent()
    )
    assert capability._caveats_downhill(_cap(**{ALLOW: ["api.example.com"]}), _parent())


def test_the_empty_set_is_the_narrowest_child_and_is_allowed() -> None:
    """ "Reach nothing" is maximal attenuation, and it is FALSY — so the flag rule would have
    refused the strictest possible child while permitting an equal one. That inversion is why
    the set clause runs before the truthiness clause rather than alongside it."""
    assert capability._caveats_downhill(_cap(**{ALLOW: []}), _parent())


def test_dropping_the_caveat_entirely_is_still_refused() -> None:
    """Presence is not weakened by the clause above: a missing key is not set-valued."""
    assert capability._caveats_downhill(_cap(), _parent()) is False


def test_a_child_that_changes_the_shape_is_refused_rather_than_coerced() -> None:
    """Fail closed on shape confusion. Coercing a string to a set would make
    `"api.example.com"` a set of 15 characters — a subset check that means nothing."""
    assert capability._caveats_downhill(_cap(**{ALLOW: "api.example.com"}), _parent()) is False
    assert capability._caveats_downhill(_cap(**{ALLOW: True}), _parent()) is False


def test_the_rule_is_keyed_on_shape_not_on_a_name_registry() -> None:
    """An enumerating caveat nobody registered still cannot widen — which is the difference
    between closing the class and closing the instance."""
    parent = _cap(unregistered_scopes=["read"])
    assert (
        capability._caveats_downhill(_cap(unregistered_scopes=["read", "write"]), parent) is False
    )
    assert capability._caveats_downhill(_cap(unregistered_scopes=["read"]), parent) is True


def test_numeric_and_flag_caveats_are_unaffected() -> None:
    """The other two shapes keep their own rules — this change adds a case, it does not
    reinterpret the existing ones."""
    assert capability._caveats_downhill(_cap(budget=5), _cap(budget=10))
    assert capability._caveats_downhill(_cap(budget=20), _cap(budget=10)) is False
    assert capability._caveats_downhill(_cap(requires_approval=True), _cap(requires_approval=True))
    assert capability._caveats_downhill(_cap(), _cap(requires_approval=True)) is False


# ── the constructor ──────────────────────────────────────────────────────────
def _content(**caveats: Any) -> dict[str, Any]:
    return capability.capability_content(
        "organ", "generated_code", grantee="prn_child", granter="prn_parent", caveats=caveats
    )


def test_attenuate_intersects_rather_than_replacing() -> None:
    """The constructor cannot BUILD a widening child, the same way it cannot build one with a
    larger budget. Relying on the validator alone would leave a hole one refactor away."""
    parent = _content(**{ALLOW: ["api.example.com", "cdn.example.com"]})
    child = capability.attenuate(
        parent,
        {ALLOW: ["cdn.example.com", "evil.example.com"]},
        "cap:parent",
        "prn_child",
        "prn_parent",
    )
    assert child["caveats"][ALLOW] == ["cdn.example.com"], "the intersection, not the request"


def test_an_attenuated_child_passes_its_own_downhill_check() -> None:
    """The two halves must agree: whatever the constructor produces, the validator accepts."""
    parent = _content(**{ALLOW: ["a", "b", "c"]})
    child = capability.attenuate(
        parent, {ALLOW: ["b", "z"]}, "cap:parent", "prn_child", "prn_parent"
    )
    assert capability._caveats_downhill(child, parent) is True
    assert child["caveats"][ALLOW] == ["b"]


def test_the_intersection_is_sorted_because_caveats_are_hashed() -> None:
    """Caveats go into the capability's content id, so an unordered set would make the same
    grant hash two ways depending on iteration order (Law 5)."""
    parent = _content(**{ALLOW: ["a", "b", "c"]})
    first = capability.attenuate(
        parent, {ALLOW: ["c", "a"]}, "cap:parent", "prn_child", "prn_parent"
    )
    second = capability.attenuate(
        parent, {ALLOW: ["a", "c"]}, "cap:parent", "prn_child", "prn_parent"
    )
    assert first["caveats"][ALLOW] == second["caveats"][ALLOW] == ["a", "c"]
