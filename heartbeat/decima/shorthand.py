"""Agent shorthand — a reversible, auditable compaction for agent↔agent messages
(SH1; CAPABILITY_MAP D2). A pointer language over **Cell IDs** plus a **signed
symbol dictionary** (itself a Cell), so inter-agent traffic gets cheaper WITHOUT
becoming an opaque private channel.

Two design rules keep it safe:

  • Lossless + deterministic. `decode(encode(x)) == x` for ANY text, including text
    that contains the codec's own sigil — a single left-to-right pass with explicit
    escaping (no fragile str.replace). The dictionary is content-addressed, so a
    given dictionary version always encodes/decodes identically.
  • Never an opaque language. The dictionary lives on the Weft as a signed Cell
    (every event is signed by its author — §`crypto`), so the mapping is public and
    tamper-evident. An INBOUND shorthand message is **decoded, logged on the Weft,
    and stored as untrusted DATA** (`instruction_eligible=False`, the recall-vs-
    instruct law) — it may be recalled, never obeyed, until something authorized
    acts on it. Shorthand is a transport, not a trust boundary.

The win: a 32-hex Cell id (or a frequent phrase) becomes a ~3-char code, so a
message that references cells and repeats common ops shrinks substantially — and
the receiver expands it against the same shared, signed dictionary.
"""
from decima.model import assert_content
from decima.hashing import content_id, nfc
from decima import memory

# Codec grammar. SIG opens a code; a code is `SIG <decimal index> ;`. A literal SIG
# in the source is escaped to `SIG SIG`. SIG is a printable-but-rare marker; the
# escaping makes the codec lossless even if the source already contains it.
SIG = "§"
DICT_TYPE = "shorthand_dict"
MESSAGE_TYPE = "message"

# Frequent agent-protocol vocabulary — registered alongside Cell ids so common ops
# compact too. Order is irrelevant to correctness (codes are by index); kept stable
# for reproducible encodings.
COMMON_PHRASES = (
    "capability", "delegate", "recall", "claim", "grant", "worker", "envelope",
    "instruction", "governance", "the loom", "objective", "evidence", "provenance",
)


class Dictionary:
    """A token↔code map. `tokens[i]` ↔ code `i`. Construct from a stored Cell with
    `load`, or build one with `from_cells` + `define`. Pure/in-memory; the durable,
    signed form is the Cell it loads from."""

    def __init__(self, name, tokens, version=0, source_cell=None):
        # de-dupe preserving order; reject tokens that would break the grammar
        seen, clean = set(), []
        for t in tokens:
            t = nfc(t)
            if not t or SIG in t or t in seen:
                continue
            seen.add(t)
            clean.append(t)
        self.name = nfc(name)
        self.tokens = clean
        self.version = version
        self.source_cell = source_cell
        # match longest token first so "the loom" beats "loom" at a position
        self._ordered = sorted(self.tokens, key=lambda t: (-len(t), t))

    # -- codec -----------------------------------------------------------------
    def _match_at(self, text, i):
        for t in self._ordered:
            if text.startswith(t, i):
                return t
        return None

    def encode(self, text: str) -> str:
        """Compact `text`: escape literal sigils, then replace dictionary tokens with
        their codes in a single greedy left-to-right pass."""
        text = nfc(text)
        out, i, n = [], 0, len(text)
        while i < n:
            if text[i] == SIG:
                out.append(SIG + SIG)            # escape a literal sigil
                i += 1
                continue
            t = self._match_at(text, i)
            if t is not None:
                out.append(f"{SIG}{self.tokens.index(t)};")
                i += len(t)
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    def decode(self, compact: str) -> str:
        """Inverse of `encode` — exact, deterministic."""
        out, i, n = [], 0, len(compact)
        while i < n:
            ch = compact[i]
            if ch == SIG:
                if i + 1 < n and compact[i + 1] == SIG:
                    out.append(SIG)              # an escaped literal sigil
                    i += 2
                    continue
                j = i + 1
                while j < n and compact[j].isdigit():
                    j += 1
                if j < n and compact[j] == ";" and j > i + 1:
                    out.append(self.tokens[int(compact[i + 1:j])])
                    i = j + 1
                    continue
                out.append(ch)                   # malformed code → treat literally
                i += 1
            else:
                out.append(ch)
                i += 1
        return "".join(out)


# ── building / storing the signed dictionary Cell ────────────────────────────
def from_cells(name, cell_ids=(), phrases=COMMON_PHRASES) -> list:
    """Ordered, unique token list for a pointer dictionary: the given Cell ids
    (the pointer language) followed by frequent phrases."""
    out, seen = [], set()
    for t in [*cell_ids, *phrases]:
        t = nfc(t)
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def define(weft, author: str, name: str, tokens) -> str:
    """Store a symbol dictionary as a Cell and return its id. The asserting event is
    signed by `author` (every Weft event is), so the mapping is signed + tamper-
    evident; re-asserting the same name with new tokens is a new VERSION of the same
    Cell (content-addressed by name)."""
    tokens = [nfc(t) for t in tokens]
    cid = content_id({"shorthand_dict": nfc(name)})
    assert_content(weft, author, cid, DICT_TYPE, {"name": nfc(name), "tokens": tokens})
    return cid


def load(weave, dict_id: str) -> Dictionary:
    """Reconstruct the Dictionary from its stored Cell (current version)."""
    c = weave.get(dict_id)
    if c is None or c.type != DICT_TYPE:
        raise ValueError(f"no shorthand dictionary at {dict_id}")
    return Dictionary(c.content["name"], c.content.get("tokens", []),
                      version=c.version, source_cell=c.id)


def signed_by(weave, weft, dict_id: str) -> list:
    """The signed events that built this dictionary Cell (author + signature),
    proving the mapping is on the Weft and attributable — not a private channel."""
    c = weave.get(dict_id)
    if c is None:
        return []
    index = {ev.id: ev for ev in weft.events()}
    return [{"author": index[eid].author, "event": eid, "sig": index[eid].sig}
            for eid in c.provenance if eid in index]


# ── measuring the saving ─────────────────────────────────────────────────────
def measure(original: str, compact: str) -> dict:
    """Byte saving (exact) and a rough subword-token estimate (~4 bytes/token, the
    usual LLM heuristic) — the headline is bytes; tokens scale with them."""
    ob, cb = len(original.encode("utf-8")), len(compact.encode("utf-8"))
    est = lambda b: max(1, round(b / 4))
    return {
        "orig_bytes": ob, "compact_bytes": cb, "saved_bytes": ob - cb,
        "byte_ratio": round(cb / ob, 3) if ob else 1.0,
        "est_orig_tokens": est(ob), "est_compact_tokens": est(cb),
    }


# ── inbound: decode, log on the Weft, store as UNTRUSTED data ────────────────
def record_inbound(weft, author: str, sender: str, compact: str,
                   dictionary: Dictionary, scope: str = memory.DEFAULT_SCOPE) -> dict:
    """Receive a shorthand message from another agent: decode it, LOG the raw +
    decoded form on the Weft (a `message` Cell), and store the decoded content as an
    UNTRUSTED claim (`instruction_eligible=False`). The message is now recallable as
    DATA and can never act as an instruction until something authorized acts on it —
    the recall-vs-instruct law, applied to inter-agent traffic. Returns the message
    + claim ids and the decoded text."""
    decoded = dictionary.decode(compact)
    msg_id = content_id({"shorthand_msg": compact, "from": nfc(sender)})
    assert_content(weft, author, msg_id, MESSAGE_TYPE, {
        "sender": nfc(sender), "compact": compact, "decoded": decoded,
        "dictionary": dictionary.source_cell,
    })
    claim = memory.remember(weft, author, decoded, msg_id,
                            instruction_eligible=False, scope=scope)
    return {"message": msg_id, "claim": claim, "decoded": decoded,
            "instruction_eligible": False}
