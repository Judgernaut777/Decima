"""PARSE1 — the untrusted-input parsing firewall.

Every byte that enters Decima from the outside — a JSON body, a CSV upload, a
config blob, a chunk of HTML, a key=value line — is **untrusted data**, and the
single most dangerous thing a system can do with untrusted input is *interpret it
with a powerful parser*. Entity-expanding XML, unsafe YAML loaders, eval, pickle: each
turns "I am parsing data" into "I am executing the attacker's program". This module
is the one confined gate the whole untrusted-input surface flows through.

The laws (CAPABILITY_MAP B2 — "Parsing — highest untrusted-input attack surface"):

  - **Only stdlib-safe parsers.** `json` (never `eval`, never `pickle`), the `csv`
    module, hand-rolled line/kv/markdown splitters, and an HTML *text extractor*
    that strips scripts and declines entity references. No XML external entities,
    no code path that can execute the payload. No eval, no exec, no pickle, no
    unsafe YAML loader appears ANYWHERE in this file.
  - **The result is DATA.** Parsed Cells are written `instruction_eligible=False`.
    An injection string sitting in a parsed field is stored verbatim AS DATA — it
    is never an instruction, never obeyed, exactly like the disposition/browser
    recall-vs-instruct law.
  - **Fail closed.** Oversized, too-deep, too-many-items, or malformed input never
    crashes and never hangs: it returns a structured **refusal** and writes a
    `parse_finding` Cell with a machine reason. A firewall that crashes is a DoS;
    a firewall that hangs is worse.
  - **Ints, not floats; everything on the Weft with provenance.** Limits and counts
    are ints. Every parse — success or refusal — lands a Cell with `source`.

Public `model` API only — no core edit. `parse(...)` of an *inbound* source can be
routed onward via `disposition.dispose(...)` (the parsed text is DATA either way).
"""
import csv
import io
import json
import re

from decima.model import assert_content, assert_edge
from decima.hashing import content_id, nfc

# ── cell types ──────────────────────────────────────────────────────────────
PARSED = "parsed"
PARSE_FINDING = "parse_finding"

# ── the safe kinds we know how to parse ─────────────────────────────────────
JSON = "json"
CSV = "csv"
KV = "kv"
MARKDOWN_LINKS = "markdown-links"
HTML_TEXT = "html-text"
KINDS = (JSON, CSV, KV, MARKDOWN_LINKS, HTML_TEXT)

# ── default limits (all ints) — a parse may attenuate, never widen, these ───
DEFAULT_LIMITS = {
    "max_bytes": 65_536,   # raw payload ceiling
    "max_depth": 32,       # nested container depth (json)
    "max_items": 4_096,    # total scalar/element count
}


class ParseRefused(Exception):
    """Internal fail-closed signal. Carries a machine reason; never escapes
    `parse()` — it is caught and turned into a structured refusal + finding."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


def _limits(limits):
    """Resolve effective limits: caller values are *intersected* downward with the
    defaults (a caller may tighten a limit, never loosen it past the firewall's
    ceiling). All values coerced to int — never float."""
    eff = dict(DEFAULT_LIMITS)
    for key, ceiling in DEFAULT_LIMITS.items():
        if limits and key in limits and limits[key] is not None:
            try:
                want = int(limits[key])
            except (TypeError, ValueError):
                raise ParseRefused("bad-limit", f"{key} not an int")
            if want < 0:
                raise ParseRefused("bad-limit", f"{key} negative")
            eff[key] = min(want, ceiling)   # attenuate-only: never wider than ceiling
    return eff


def _as_bytes(raw):
    """Untrusted input arrives as bytes or str; normalize to bytes for the size
    gate, then to text. Decode strictly so malformed UTF-8 fails closed, never
    silently mangles."""
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, str):
        return raw.encode("utf-8", "surrogatepass")
    raise ParseRefused("bad-type", f"raw must be bytes or str, got {type(raw).__name__}")


def _decode(blob, lim):
    if len(blob) > lim["max_bytes"]:
        raise ParseRefused("oversized", f"{len(blob)} > max_bytes={lim['max_bytes']}")
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ParseRefused("malformed", f"invalid utf-8: {e}")


# ── JSON: depth + item bounded, json.loads only (never eval/pickle) ─────────
def _check_json_shape(obj, lim, depth=0, counter=None):
    if counter is None:
        counter = [0]
    if depth > lim["max_depth"]:
        raise ParseRefused("too-deep", f"depth>{lim['max_depth']}")
    if isinstance(obj, dict):
        for v in obj.values():
            counter[0] += 1
            if counter[0] > lim["max_items"]:
                raise ParseRefused("too-many-items", f"items>{lim['max_items']}")
            _check_json_shape(v, lim, depth + 1, counter)
    elif isinstance(obj, list):
        for v in obj:
            counter[0] += 1
            if counter[0] > lim["max_items"]:
                raise ParseRefused("too-many-items", f"items>{lim['max_items']}")
            _check_json_shape(v, lim, depth + 1, counter)
    return counter[0]


def _parse_json(text, lim):
    try:
        # json.loads — pure stdlib parser. No object_hook that could execute,
        # no eval, no pickle. parse_constant rejects NaN/Infinity (non-portable).
        obj = json.loads(text, parse_constant=_reject_constant)
    except (json.JSONDecodeError, ValueError, RecursionError) as e:
        raise ParseRefused("malformed", f"json: {e}")
    n = _check_json_shape(obj, lim)
    return {"value": obj, "items": int(n)}


def _reject_constant(c):
    raise ValueError(f"non-finite constant {c!r} rejected")


# ── CSV: csv module, row/cell bounded ───────────────────────────────────────
def _parse_csv(text, lim):
    rows = []
    cells = 0
    try:
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            cells += len(row)
            if cells > lim["max_items"]:
                raise ParseRefused("too-many-items", f"cells>{lim['max_items']}")
            rows.append([nfc(c) for c in row])
            if len(rows) > lim["max_items"]:
                raise ParseRefused("too-many-items", f"rows>{lim['max_items']}")
    except csv.Error as e:
        raise ParseRefused("malformed", f"csv: {e}")
    return {"rows": rows, "items": int(cells)}


# ── KV: simple key=value lines (config-blob shape) ──────────────────────────
def _parse_kv(text, lim):
    pairs = {}
    n = 0
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if "=" not in ln:
            raise ParseRefused("malformed", f"kv line without '=': {ln[:40]!r}")
        n += 1
        if n > lim["max_items"]:
            raise ParseRefused("too-many-items", f"pairs>{lim['max_items']}")
        key, _, val = ln.partition("=")
        pairs[nfc(key.strip())] = nfc(val.strip())
    return {"pairs": pairs, "items": int(n)}


# ── markdown links: extract [text](url) — DATA, URLs never auto-followed ────
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def _parse_markdown_links(text, lim):
    links = []
    for m in _MD_LINK_RE.finditer(text):
        if len(links) >= lim["max_items"]:
            raise ParseRefused("too-many-items", f"links>{lim['max_items']}")
        links.append({"text": nfc(m.group(1)), "url": nfc(m.group(2))})
    return {"links": links, "items": int(len(links))}


# ── HTML → text: strip scripts/styles, decline entity references ────────────
# A *text extractor*, not an HTML parser. We never build a DOM, never resolve
# entities (so no entity-expansion / billion-laughs), never run scripts. Whole
# <script>/<style> blocks (and their contents) are removed, remaining tags are
# stripped, and any '&...;' reference is left literal (declined), not expanded.
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b[^>]*>.*?</\1\s*>")
_TAG_RE = re.compile(r"(?s)<[^>]*>")
_ENTITY_RE = re.compile(r"&[#0-9A-Za-z]+;")


def _parse_html_text(text, lim):
    if len(text) > lim["max_bytes"]:
        raise ParseRefused("oversized", "html exceeds max_bytes")
    had_script = bool(_SCRIPT_STYLE_RE.search(text))
    stripped = _SCRIPT_STYLE_RE.sub(" ", text)
    declined = bool(_ENTITY_RE.search(stripped))      # entities present → declined, not expanded
    no_tags = _TAG_RE.sub(" ", stripped)
    # leave entity refs LITERAL — we decline rather than expand them
    clean = re.sub(r"[ \t\r\f\v]+", " ", no_tags).strip()
    clean = re.sub(r"\n\s*\n+", "\n", clean)
    if len(clean) > lim["max_bytes"]:
        raise ParseRefused("oversized", "extracted text exceeds max_bytes")
    return {
        "text": nfc(clean),
        "scripts_stripped": bool(had_script),
        "entities_declined": bool(declined),
        "items": int(len(clean.split())),
    }


_PARSERS = {
    JSON: _parse_json,
    CSV: _parse_csv,
    KV: _parse_kv,
    MARKDOWN_LINKS: _parse_markdown_links,
    HTML_TEXT: _parse_html_text,
}


def _record_finding(k, author, source, kind, reason, detail):
    """A fail-closed refusal is itself evidence — land a `parse_finding` Cell on
    the Weft so the refusal is auditable (DET1-style signal). DATA, not obeyed."""
    fid = content_id({"parse_finding": reason, "kind": kind,
                      "source": source, "at": k.weft.head})
    assert_content(k.weft, author, fid, PARSE_FINDING, {
        "kind": kind, "source": nfc(str(source)),
        "reason": reason, "detail": nfc(str(detail)),
        "refused": True, "instruction_eligible": False,
    })
    return fid


def parse(k, kind, raw, *, source, limits=None, author=None) -> dict:
    """Safely parse `kind` ∈ KINDS from untrusted `raw` into structured `parsed`
    Cells flagged `instruction_eligible=False`, enforcing byte/depth/item limits.

    Returns a dict:
      on success — {"ok": True,  "kind", "cell", "parsed", "items", "source"}
      on refusal — {"ok": False, "kind", "finding", "reason", "detail", "source"}

    NEVER raises on bad/oversized/too-deep/malformed input and NEVER hangs: such
    input fails closed to a refusal + a `parse_finding` Cell. The parsed payload
    is DATA — route it onward via `disposition.dispose` if it's an inbound source.
    Only stdlib-safe parsers are used (json/csv/regex line splitters); no eval,
    no exec, no pickle, no XML entity expansion anywhere in this module."""
    author = author or k.decima_agent_id

    if kind not in _PARSERS:
        fid = _record_finding(k, author, source, kind, "unsupported-kind",
                              f"{kind!r} not in {sorted(KINDS)}")
        return {"ok": False, "kind": kind, "finding": fid,
                "reason": "unsupported-kind", "detail": kind, "source": source}

    try:
        lim = _limits(limits)
        blob = _as_bytes(raw)
        text = _decode(blob, lim)
        result = _PARSERS[kind](text, lim)
    except ParseRefused as r:
        fid = _record_finding(k, author, source, kind, r.reason, r.detail)
        return {"ok": False, "kind": kind, "finding": fid,
                "reason": r.reason, "detail": r.detail, "source": source}
    except RecursionError:
        # belt-and-suspenders: a pathological structure that beats the depth gate
        # still fails closed rather than crashing the process.
        fid = _record_finding(k, author, source, kind, "too-deep", "recursion limit")
        return {"ok": False, "kind": kind, "finding": fid,
                "reason": "too-deep", "detail": "recursion", "source": source}

    items = int(result.pop("items", 0))
    cid = content_id({"parsed": kind, "source": source, "result": repr(result),
                      "at": k.weft.head})
    assert_content(k.weft, author, cid, PARSED, {
        "kind": kind, "source": nfc(str(source)),
        "parsed": result, "items": items,
        "instruction_eligible": False,        # the firewall's law: parsed input is DATA
        "recallable": True, "citable": True,
    })
    # provenance edge: the parsed Cell derives_from its source intake (if one was given)
    assert_edge(k.weft, author, cid, "parsed_from", nfc(str(source)))
    return {"ok": True, "kind": kind, "cell": cid,
            "parsed": result, "items": items, "source": source}
