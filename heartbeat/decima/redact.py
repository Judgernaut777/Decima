"""Privacy redaction + classification — the fail-closed gate UPSTREAM of the router.

VISION "Advanced model strategy — compose, not replace" gives Decima a privacy=private
tier RULE (`router._r_private` forces the on-device lane), but a rule is not enough: raw
task text can still carry live secrets that must never leave the device *at all*, and the
router chooses a tier — it never inspects the bytes. This module is the missing upstream
scrubber. It runs BEFORE any task text can reach a non-local (external) provider and it
COMPOSES OVER `router.py` — it never edits it.

Three deterministic, offline capabilities, built on the `reckoner.scan` / `detection`
token-and-regex idiom (stdlib `re` only):

  1. scrub(text) -> (scrubbed_text, findings)
     Detect + replace API keys, JWTs, SSH private keys, DB connection URLs, bearer tokens,
     emails, internal hostnames and local filesystem paths with a TYPED placeholder
     (`<REDACTED:api_key:1>`). Deterministic: same input → identical output. The scrubbed
     bytes of a detected secret do NOT survive into the output.

  2. classify_privacy(text, findings) -> public | low_sensitive | repo_sensitive |
     secret_sensitive | restricted
     A repo_sensitive / restricted task maps to the local-only lane; a secret_sensitive
     task (a RAW high-value secret is present — a live key, a private key, a DB URL) is
     BLOCKED from ALL external routing. FAIL CLOSED: `external_permitted` is False and the
     boundary guard `admit` RAISES `RedactionBlocked` — never best-effort lets it through.

  3. record_redaction(k, ...) writes a provenance Cell on the Weft capturing the CLASSES +
     COUNTS of what was found (all ints) — NEVER the secret values themselves.

Untrusted-is-data: the text is DATA throughout. Redaction neither executes nor trusts it —
it only READS bytes and rewrites them, exactly as a detection reads a Cell. This module
holds NO keyring, mints NO grant, touches `authorize` NEVER — like the router it confers
ZERO authority. Blocking an external route is a refusal to hand DATA to an engine; it is
not a capability decision (`capability.authorize` + Morta still gate every real effect).
"""
import re

from decima.hashing import content_id
from decima import model


# ── privacy classes (ordered least → most sensitive) ─────────────────────────
PUBLIC = "public"
LOW_SENSITIVE = "low_sensitive"        # PII (e.g. an email) — scrub, external OK
REPO_SENSITIVE = "repo_sensitive"      # infra/repo markers — keep local-only
RESTRICTED = "restricted"              # explicitly restricted/classified — local-only
SECRET_SENSITIVE = "secret_sensitive"  # a RAW high-value secret present — BLOCK external
CLASSES = (PUBLIC, LOW_SENSITIVE, REPO_SENSITIVE, RESTRICTED, SECRET_SENSITIVE)

# Finding kinds whose PRESENCE means a raw high-value secret is in the text. Their mere
# presence (before scrubbing) makes the task secret_sensitive → blocked from ALL external
# routing. We do not trust the scrubber to have caught every variant: a task that carries a
# live credential is refused wholesale (fail closed), not "sent once we think it's clean".
HIGH_VALUE_KINDS = frozenset({"api_key", "jwt", "ssh_private_key", "db_url", "bearer_token"})
# Kinds that mark internal/infrastructure detail — sensitive enough to pin to local-only.
REPO_KINDS = frozenset({"internal_hostname", "fs_path"})
# Kinds that are ordinary PII — scrub them, but external routing is still permitted.
PII_KINDS = frozenset({"email"})

# Explicit human markers of a restricted document (no secret token needed to be restricted).
_RESTRICTED_MARKERS = ("restricted", "classified", "top secret", "nda-only",
                       "attorney-client", "do not distribute")

# ── the shared "live status" privacy_tier vocabulary (loose-coupling contract) ─
# A provider Cell carries a privacy_tier. These two names are the LOCAL side (data stays on
# device or in a private-rented enclave); the other two are the EXTERNAL side that this gate
# guards. No lane imports another — they agree only on these strings.
LOCAL_TIERS = frozenset({"local_only", "private_rented"})
EXTERNAL_TIERS = frozenset({"external", "external_paid"})


class RedactionBlocked(Exception):
    """Fail-closed refusal: a classification forbids handing this task's text to an
    external provider. Carries the classification and the classes found (never values)."""

    def __init__(self, classification, classes, message=None):
        self.classification = classification
        self.classes = tuple(classes)
        super().__init__(message or
                         f"redaction blocks external routing: {classification} "
                         f"(classes={list(self.classes)})")


# ── detectors: ordered (kind, compiled regex) — HIGH-VALUE FIRST ─────────────
# Order matters and is load-bearing: each detected span is replaced with a placeholder
# BEFORE the next detector runs, so a broad pattern can never re-match inside an already
# scrubbed value, and a value that belongs to two families (a JWT used as a bearer token,
# an email whose domain is an internal host) is redacted under the more specific family.
# Placeholders (`<REDACTED:...>`) contain no secret bytes and match no detector, so
# scrubbing is a fixed point.
_DETECTORS = (
    # A PEM private key block — the whole BEGIN…END span (DOTALL). Highest value.
    ("ssh_private_key",
     re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
                re.DOTALL)),
    # A database connection URL WITH embedded credentials (user:pass@host/db).
    ("db_url",
     re.compile(r"\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp|mssql)://"
                r"[^\s'\"<>]+")),
    # A bearer/authorization token (captures a JWT-shaped value used as a bearer).
    ("bearer_token",
     re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/\-]+=*")),
    # A JSON Web Token: three base64url segments, the first two starting `eyJ`.
    ("jwt",
     re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    # Well-known API-key shapes (provider prefixes) + a generic `key = <long-token>` form.
    ("api_key",
     re.compile(r"\b(?:sk-[A-Za-z0-9]{20,}"
                r"|AKIA[0-9A-Z]{16}"
                r"|AIza[0-9A-Za-z_\-]{20,}"
                r"|ghp_[A-Za-z0-9]{20,}"
                r"|xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    # An email address (ordinary PII).
    ("email",
     re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    # An internal hostname (infra detail): something.<internal-ish-suffix>.
    ("internal_hostname",
     re.compile(r"\b[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*"
                r"\.(?:internal|intranet|corp|local|lan|svc|cluster\.local)\b")),
    # A local filesystem path rooted at a well-known directory (leaks host layout).
    ("fs_path",
     re.compile(r"(?<![\w/])/(?:home|Users|etc|var|opt|usr|srv|root|mnt|tmp)"
                r"/[A-Za-z0-9._\-/]+")),
)


def scrub(text):
    """Detect and replace secrets/PII with TYPED placeholders. Returns
    `(scrubbed_text, findings)`.

    Deterministic: the detectors run in a fixed order and each unique matched value gets a
    stable per-kind index in first-appearance order, so the same input always yields byte-
    identical output. `findings` is a list of `{"kind", "placeholder", "length"}` records —
    it records the LENGTH of each redacted secret (an int), never its bytes."""
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    findings = []
    for kind, rx in _DETECTORS:
        counter = 0
        seen = {}  # matched value → placeholder, so equal values share one index

        def _sub(m, kind=kind, seen=seen):
            nonlocal counter
            val = m.group(0)
            ph = seen.get(val)
            if ph is None:
                counter += 1
                ph = f"<REDACTED:{kind}:{counter}>"
                seen[val] = ph
                findings.append({"kind": kind, "placeholder": ph, "length": len(val)})
            return ph

        text = rx.sub(_sub, text)
    return text, findings


def classify_privacy(text, findings):
    """Classify the task from the markers present → one of `CLASSES`. Precedence is
    highest-sensitivity-first so the strongest signal wins:

      raw high-value secret  → secret_sensitive  (BLOCK external — fail closed)
      explicit restricted    → restricted        (local-only)
      infra/repo markers     → repo_sensitive     (local-only)
      PII                    → low_sensitive       (scrub; external OK)
      nothing                → public              (external OK)"""
    kinds = {f.get("kind") for f in findings}
    low = (text or "").lower()
    if kinds & HIGH_VALUE_KINDS:
        return SECRET_SENSITIVE
    if any(m in low for m in _RESTRICTED_MARKERS):
        return RESTRICTED
    if kinds & REPO_KINDS:
        return REPO_SENSITIVE
    if kinds & PII_KINDS:
        return LOW_SENSITIVE
    return PUBLIC


def external_permitted(classification):
    """The FAIL-CLOSED core: True only when handing the (scrubbed) text to an external
    provider is allowed. secret_sensitive is blocked outright; repo_sensitive and restricted
    are pinned to the local-only lane, so they too are refused the external path. Only public
    and low_sensitive may leave the device. Default-deny: an unknown class returns False."""
    return classification in (PUBLIC, LOW_SENSITIVE)


def to_router_privacy(classification):
    """Map a privacy class onto `router.py`'s `TaskDescriptor.privacy` field, so the
    existing `router._r_private` hard-rule pins a sensitive task to the on-device lane —
    composition over the router, not a replacement.

      secret_sensitive / repo_sensitive / restricted → 'private'  (router → local-small)
      low_sensitive                                   → 'sensitive'
      public                                          → 'public'"""
    if classification in (SECRET_SENSITIVE, REPO_SENSITIVE, RESTRICTED):
        return "private"
    if classification == LOW_SENSITIVE:
        return "sensitive"
    return "public"


REDACTION = "redaction"


def record_redaction(k, findings, classification, *, author=None, meta=None):
    """Write a provenance Cell (type `redaction`) on the Weft capturing the CLASSES and
    per-class COUNTS of what was found — all ints, NEVER the secret values. Returns the
    cell id. `author` defaults to the Reckoner (the scanning authority, as in detection)."""
    author = author or k.reckoner.id
    counts = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    classes = sorted(counts)
    content = {
        "classes": classes,
        "counts": {kind: int(counts[kind]) for kind in classes},
        "total": int(sum(counts.values())),
        "classification": classification,
        "external_permitted": bool(external_permitted(classification)),
    }
    if meta:  # optional ints-only metadata (e.g. text lengths) — no free text
        content["meta"] = {key: int(val) for key, val in meta.items()}
    cid = content_id({"redaction": classification, "counts": content["counts"],
                      "lamport": k.weft.lamport})
    model.assert_content(k.weft, author, cid, REDACTION, content)
    return cid


class Screening:
    """The result of screening a task's text at the external boundary."""

    def __init__(self, scrubbed, findings, classification, record=None):
        self.scrubbed = scrubbed
        self.findings = findings
        self.classification = classification
        self.external_permitted = external_permitted(classification)
        self.record = record  # the redaction Cell id, if one was written

    @property
    def classes(self):
        return sorted({f["kind"] for f in self.findings})


def screen(text, k=None, *, author=None, record=True):
    """Scrub + classify a task's text and (optionally) record the redaction on the Weft.
    Pure w.r.t. authority — it produces DATA and a provenance Cell, never an effect."""
    scrubbed, findings = scrub(text)
    classification = classify_privacy(text, findings)
    rec = None
    if k is not None and record:
        rec = record_redaction(k, findings, classification, author=author,
                               meta={"text_len": len(text or ""), "scrubbed_len": len(scrubbed)})
    return Screening(scrubbed, findings, classification, record=rec)


def admit(text, provider, k=None, *, author=None, record=True):
    """The BOUNDARY GUARD. Screen `text` for a `provider` (a live-status dict carrying a
    `privacy_tier`) and return the scrubbed text that may be sent — or FAIL CLOSED.

    For an EXTERNAL provider (`privacy_tier` in `EXTERNAL_TIERS`), a task whose class is not
    `external_permitted` — any secret_sensitive / repo_sensitive / restricted task — RAISES
    `RedactionBlocked`: the external path is prevented, not attempted best-effort. A local
    provider always receives the scrubbed text (defence in depth: scrub even on-device).

    Returns `(scrubbed_text, Screening)`."""
    s = screen(text, k, author=author, record=record)
    tier = provider.get("privacy_tier") if isinstance(provider, dict) else provider
    if tier in EXTERNAL_TIERS and not s.external_permitted:
        raise RedactionBlocked(s.classification, s.classes)
    return s.scrubbed, s
