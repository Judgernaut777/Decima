"""Declarative manifests for the ~25 BUNDLED real engines — the out-of-box catalog.

Decima ships ~25 hand-wrapped real engines (stripe_rail, comms, kyc, weather_engine,
…), each with its OWN install path (it registers a live capability via
`kernel.integrate_tool` when wired). Those install paths make the engines RUNNABLE, but
they do not make the built-in set UNIFORM or DISCOVERABLE: nothing describes the whole
catalog in one place, so `discovery.search`/`discover` — the plug-in-or-forge front door
— cannot find a real engine before forging a new one.

This module closes that gap. It is a DESCRIPTIVE layer: a declarative table `BUILTINS`
names each bundled engine with a clear natural-language description, its ONEX archetype
(EFFECT for outward rails, COMPUTE for read/record engines), its effect_class (how it is
gated), Morta caveats (`requires_approval` for the outward/financial rails), and tags.
`register_builtins(k)` folds one `capability_manifest` per engine onto the Weft
(source="builtin") so the built-in capabilities show up in the registry and rank in
discovery for real goals — WITHOUT touching a single engine module or any core file.

A manifest GRANTS NOTHING (manifest.py, Law): registering a description confers no
authority — the engines keep their own gated install paths; this only makes them
FINDABLE + uniform. Idempotent: manifests are content-addressed (`manifest_id`), so
re-registering the identical content is a no-op. Pure stdlib; ints only; composes the
public `manifest` API. No core edit, no engine edit.
"""
from decima import manifest as M

# Convenience gate markers — a manifest only ever TIGHTENS a gate (manifest.py).
_APPROVE = {"requires_approval": True}     # Morta-gated outward / financial effect.


# The declarative catalog: one spec per bundled engine module under decima/. The `name`
# is the engine's module name so the manifest and the engine line up 1:1. Descriptions
# are natural-language sentences (with the words a caller's GOAL would use) so the
# deterministic lexical index in discovery.py ranks the right engine for a real goal.
BUILTINS = [
    # ── Outward FINANCIAL rails (money moves; Morta-gated) ─────────────────────────
    {"name": "stripe_rail", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Charge a customer's credit card and accept a card payment through "
                    "the Stripe payment rail.",
     "tags": ["stripe", "charge", "credit", "card", "payment", "customer", "billing"]},
    {"name": "payouts", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Send money out to a bank account as an ACH payout or bank transfer "
                    "(disburse funds to a payee).",
     "tags": ["payout", "ach", "bank", "transfer", "disburse", "money", "withdrawal"]},
    {"name": "brokerage_engine", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Place a stock or securities trade order (buy or sell shares) with a "
                    "regulated brokerage.",
     "tags": ["brokerage", "stock", "securities", "trade", "buy", "sell", "shares", "order"]},
    {"name": "exchange", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Place a cryptocurrency trade order to buy or sell crypto on a coin "
                    "exchange.",
     "tags": ["crypto", "cryptocurrency", "exchange", "bitcoin", "trade", "buy", "sell", "coin"]},
    {"name": "payroll", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Run payroll and pay employees their wages for a pay period through a "
                    "payroll provider.",
     "tags": ["payroll", "wages", "salary", "employees", "pay", "gusto", "adp"]},
    {"name": "shipping", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Buy a postage shipping label and create a parcel shipment with a "
                    "carrier, with a tracking number.",
     "tags": ["shipping", "postage", "label", "parcel", "shipment", "tracking", "carrier"]},
    {"name": "cloud_compute", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Provision a cloud server compute instance (boot a virtual machine) "
                    "on a cloud provider.",
     "tags": ["cloud", "compute", "server", "instance", "vm", "provision", "ec2"]},
    {"name": "ecommerce", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Place a customer purchase order on an e-commerce store for "
                    "fulfilment (line items, quantities, and order total).",
     "tags": ["ecommerce", "order", "purchase", "store", "shop", "fulfilment", "checkout"]},
    {"name": "ads", "archetype": "EFFECT", "effect_class": "FINANCIAL",
     "caveats": _APPROVE,
     "description": "Launch a paid advertising campaign with a marketing budget on an ad "
                    "platform.",
     "tags": ["ads", "advertising", "campaign", "marketing", "budget", "promote"]},

    # ── Outward COMMUNICATION rails (words leave the box; Morta-gated) ──────────────
    {"name": "comms", "archetype": "EFFECT", "effect_class": "COMMUNICATION",
     "caveats": _APPROVE,
     "description": "Send a text message (SMS) or send an email to a person through a "
                    "real messaging carrier.",
     "tags": ["sms", "text", "message", "email", "send", "notify", "twilio", "sendgrid"]},
    {"name": "paging", "archetype": "EFFECT", "effect_class": "COMMUNICATION",
     "caveats": _APPROVE,
     "description": "Page an on-call engineer and open an incident alert on a paging / "
                    "escalation platform.",
     "tags": ["page", "pager", "incident", "oncall", "alert", "escalation", "pagerduty"]},

    # ── Outward LEGAL rails (a binding submission; Morta-gated) ─────────────────────
    {"name": "esign", "archetype": "EFFECT", "effect_class": "LEGAL", "caveats": _APPROVE,
     "description": "Send a document out for a legally binding electronic signature "
                    "(an e-signature envelope to signers).",
     "tags": ["esign", "esignature", "signature", "sign", "document", "contract", "docusign"]},
    {"name": "insurance_claim", "archetype": "EFFECT", "effect_class": "LEGAL",
     "caveats": _APPROVE,
     "description": "File an insurance claim with a carrier — submit a loss, a claimed "
                    "amount, and evidence.",
     "tags": ["insurance", "claim", "file", "filing", "carrier", "loss", "coverage"]},

    # ── Outward INFRA rail (a live infra change; Morta-gated) ───────────────────────
    {"name": "dns", "archetype": "EFFECT", "effect_class": "INFRA", "caveats": _APPROVE,
     "description": "Apply a DNS record change or register a domain name through a DNS / "
                    "domains provider.",
     "tags": ["dns", "domain", "record", "zone", "registrar", "route53", "namecheap"]},

    # ── Outward IDENTITY / SCHEDULING rails (an effect, but not money) ──────────────
    {"name": "oidc", "archetype": "EFFECT", "effect_class": "IDENTITY",
     "description": "Authenticate a user and sign them in through an OAuth / OpenID "
                    "Connect single sign-on token exchange.",
     "tags": ["auth", "login", "signin", "oauth", "oidc", "sso", "token", "authenticate"]},
    {"name": "calendar_engine", "archetype": "EFFECT", "effect_class": "SCHEDULING",
     "description": "Create a calendar event or book a meeting on a real calendar the "
                    "attendees watch.",
     "tags": ["calendar", "event", "meeting", "booking", "schedule", "appointment"]},

    # ── COMPUTE read/record engines (read a fact / record a reference; auto-allowed) ─
    {"name": "tax_engine", "archetype": "COMPUTE", "effect_class": "READ",
     "description": "Calculate the sales tax owed on a taxable transaction using a real "
                    "tax-rate provider.",
     "tags": ["tax", "sales-tax", "vat", "calculate", "rate", "taxjar", "avalara"]},
    {"name": "kyc", "archetype": "COMPUTE", "effect_class": "IDENTITY",
     "description": "Verify a person's identity by running a KYC identity-verification "
                    "check on their documents.",
     "tags": ["kyc", "identity", "verify", "verification", "aml", "onboarding", "persona"]},
    {"name": "background_check", "archetype": "COMPUTE", "effect_class": "COMPLIANCE",
     "description": "Run an employment background check and criminal-records screening "
                    "on a job candidate.",
     "tags": ["background", "check", "screening", "criminal", "employment", "fcra", "candidate"]},
    {"name": "accounting", "archetype": "COMPUTE", "effect_class": "READ",
     "description": "Post a bookkeeping journal entry or an invoice to the accounting "
                    "books (a QuickBooks/Xero-style ledger).",
     "tags": ["accounting", "bookkeeping", "journal", "invoice", "ledger", "books", "quickbooks"]},
    {"name": "maps_engine", "archetype": "COMPUTE", "effect_class": "READ",
     "description": "Geocode a street address into latitude and longitude map "
                    "coordinates using a maps provider.",
     "tags": ["maps", "geocode", "address", "coordinates", "latitude", "longitude", "location"]},
    {"name": "weather_engine", "archetype": "COMPUTE", "effect_class": "READ",
     "description": "Get the current weather and the forecast conditions for a location.",
     "tags": ["weather", "forecast", "temperature", "conditions", "climate", "rain"]},
    {"name": "cloud_storage", "archetype": "COMPUTE", "effect_class": "STORAGE",
     "description": "Store a file or upload a blob to cloud object storage (an S3/GCS-"
                    "style bucket) and record a reference.",
     "tags": ["storage", "file", "upload", "blob", "bucket", "object", "s3", "backup"]},
    {"name": "ocr_engine", "archetype": "COMPUTE", "effect_class": "READ",
     "description": "Extract the text from a scanned document or image using an OCR "
                    "document-recognition engine.",
     "tags": ["ocr", "extract", "text", "scan", "document", "recognition", "textract"]},
    {"name": "translate_engine", "archetype": "COMPUTE", "effect_class": "READ",
     "description": "Translate text from one language into another with a machine-"
                    "translation provider.",
     "tags": ["translate", "translation", "language", "localize", "deepl", "machine-translation"]},
]


def register_builtins(k) -> list:
    """Register a descriptive `capability_manifest` for every bundled engine in
    `BUILTINS` onto the Weft (source="builtin"), returning the manifest cell ids.

    This makes the out-of-box engine set uniform + discoverable: after this call the
    manifest `registry` contains one entry per engine and `discovery.search`/`discover`
    can rank a real engine for a natural-language goal (plug-in before forge). It grants
    NO authority — each engine keeps its own gated install path. Idempotent: manifests
    are content-addressed, so re-registering identical content simply re-lands the same
    cell id."""
    ids = []
    for spec in BUILTINS:
        m = M.capability_manifest(
            spec["name"],
            description=spec["description"],
            archetype=spec["archetype"],
            effect_class=spec["effect_class"],
            caveats=spec.get("caveats"),
            tags=spec.get("tags"),
            source="builtin",
        )
        ids.append(M.register(k, m))
    return ids
