"""NFC normalization on EVERY nested signed field — cross-implementation hash agreement.

Weft Protocol §1 requires signed text to be UTF-8, NFC-normalized. The Heartbeat
normalized human text only at the `say` boundary; programmatic content that never
crossed `say` could carry non-NFC text, so the SAME logical content in two Unicode
normalization forms would hash to two different ids — breaking dedup and the "same
content, same id, everywhere" guarantee a second implementation must honor.

This closes it in one place: `hashing.canonical` NFC-normalizes every nested string
(dict keys + values, list items, any depth) before hashing, and `weft.append`
normalizes the stored body so folded content is canonical too. Idempotent for ASCII /
already-normalized content, so no existing id changes.

This check proves:
  - content_id is NFC-INVARIANT: decomposed vs composed forms of the same text — as a
    value, as a KEY, and nested deep — hash to the SAME id;
  - genuinely different text still differs (no false collisions);
  - a cell asserted with DECOMPOSED text folds to CANONICAL NFC (composed) content —
    normalization is enforced on the stored field, not just the hash;
  - identity is normalization-independent: a cell addressed by content_id of composed
    vs decomposed text is the SAME cell (dedup across forms).

Contract: run(k, line). Fail loud.
"""
import os
import tempfile

from decima.kernel import Kernel
from decima.hashing import content_id
from decima import model

# "café" in two Unicode forms — SAME text, DIFFERENT bytes. Built from escapes so the
# distinction survives any source-file normalization.
COMPOSED = "café"        # é as one code point (NFC)
DECOMPOSED = "café"     # e + U+0301 combining acute (NFD)
COMBINING = "́"


def run(k, line):
    line("\n== NFC ON EVERY NESTED SIGNED FIELD (hash agreement, WEFT §1) ==")
    assert COMPOSED != DECOMPOSED and len(COMPOSED) != len(DECOMPOSED), "test data must differ byte-wise"

    # 1. content_id is NFC-invariant — value, key, and deep nesting. ───────────────────
    assert content_id({"t": DECOMPOSED}) == content_id({"t": COMPOSED}), "value not normalized"
    assert content_id({DECOMPOSED: 1}) == content_id({COMPOSED: 1}), "dict KEY not normalized"
    deep_d = content_id({"a": [DECOMPOSED, {"b": DECOMPOSED, "n": 3}]})
    deep_c = content_id({"a": [COMPOSED, {"b": COMPOSED, "n": 3}]})
    assert deep_d == deep_c, "nested string not normalized"
    line("  content_id NFC-invariant across value, key, and deep nesting ✓")

    # 2. Genuinely different text still differs (no false collision). ──────────────────
    assert content_id({"t": COMPOSED}) != content_id({"t": "cafe"}), "must not over-collide"
    line("  distinct text still hashes distinctly (no false collision) ✓")

    # 3. append folds DECOMPOSED input to CANONICAL NFC content (stored field). ────────
    kk = Kernel(os.path.join(tempfile.mkdtemp(), "weft.db"), fresh=True)
    cid = content_id({"note": "n1"})
    model.assert_content(kk.weft, kk.human.id, cid, "note", {"text": DECOMPOSED})
    cell = kk.weave().get(cid)
    assert cell is not None and cell.content["text"] == COMPOSED, cell.content
    assert COMBINING not in cell.content["text"], "the combining mark must be gone (NFC)"
    line("  a cell asserted with DECOMPOSED text folds to canonical NFC (composed) ✓")

    # 4. Identity is normalization-independent — same cell across forms. ───────────────
    id_from_composed = content_id({"about": COMPOSED})
    id_from_decomposed = content_id({"about": DECOMPOSED})
    assert id_from_composed == id_from_decomposed
    model.assert_content(kk.weft, kk.human.id, id_from_decomposed, "person", {"name": DECOMPOSED})
    # A second write addressed by the COMPOSED form lands on the SAME cell (LWW overwrite).
    model.assert_content(kk.weft, kk.human.id, id_from_composed, "person", {"name": COMPOSED, "v": 2})
    same = kk.weave().get(id_from_composed)
    assert same is not None and same.content.get("v") == 2, "both forms must address one cell"
    assert len([c for c in kk.weave().of_type("person") if c.id == id_from_composed]) == 1
    line("  composed and decomposed forms address ONE cell — dedup across normalization ✓")

    line("  → text is NFC-normalized on every nested field (hash AND stored content); the "
         "id of a payload is its Unicode-normalized identity, so a second implementation "
         "agrees on ids without accident.")
