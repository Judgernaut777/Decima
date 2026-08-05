//! Content addressing — Law 4: identity is content + cause.
//!
//! Heartbeat profile of Weft Protocol v0.1 §1 (see heartbeat/PROFILE.md):
//!   - hash: BLAKE2b-128 (digest_size = 16)
//!   - canonical bytes: sorted-key JSON in UTF-8, separators (",", ":"),
//!     ensure_ascii=False (raw UTF-8), NFC-normalized recursively, no floats,
//!     arbitrary-precision integers
//!   - domain separation: digest = HASH("decima:v0.1:" || kind || 0x00 || bytes)
//!
//! Port of heartbeat/decima/hashing.py.

use blake2::digest::consts::U16;
use blake2::{Blake2b, Digest};
use serde_json::Value;
use unicode_normalization::UnicodeNormalization;

const DOMAIN: &[u8] = b"decima:v0.1:";

/// Recursively NFC-normalize EVERY string — map keys and values, list items,
/// nested arbitrarily deep. Non-string scalars pass through untouched.
/// Idempotent (hashing.nfc_deep).
pub fn nfc_deep(v: &Value) -> Value {
    match v {
        Value::String(s) => Value::String(s.chars().nfc().collect()),
        Value::Array(items) => Value::Array(items.iter().map(nfc_deep).collect()),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(k, val)| (k.chars().nfc().collect(), nfc_deep(val)))
                .collect(),
        ),
        other => other.clone(),
    }
}

/// NFC-normalize human text before it enters the Weft (hashing.nfc).
pub fn nfc(text: &str) -> String {
    text.chars().nfc().collect()
}

/// Deterministic byte encoding: UTF-8, sorted keys, no whitespace, NFC text
/// throughout (hashing.canonical).
///
/// serde_json::Value without the `preserve_order` feature stores objects in a
/// BTreeMap (sorted keys), serializes with no whitespace, escapes only the
/// same characters Python's json.dumps does with ensure_ascii=False, and —
/// with the `arbitrary_precision` feature — round-trips integer literals of
/// any size verbatim. Verified byte-for-byte against the golden vectors.
pub fn canonical(payload: &Value) -> Vec<u8> {
    let normalized = nfc_deep(payload);
    serde_json::to_string(&normalized)
        .expect("serde_json::Value serialization is infallible")
        .into_bytes()
}

/// The STORED payload bytes of a Weft row: Python's
/// `json.dumps(payload, sort_keys=True)` with the DEFAULT separators
/// (", ", ": ") and ensure_ascii=True (weft.py `append`/`ingest`). This is
/// NOT `canonical()` — the content id hashes the compact raw-UTF-8 form,
/// while the SQLite `payload` TEXT column stores this spaced, ASCII-escaped
/// form. Non-ASCII becomes `\uXXXX` (lowercase hex; astral chars as UTF-16
/// surrogate pairs), exactly like CPython's json encoder.
pub fn python_dumps_sorted(v: &Value) -> String {
    let mut out = String::new();
    dump_value(v, &mut out);
    out
}

fn dump_value(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&n.to_string()),
        Value::String(s) => dump_string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                dump_value(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            // serde_json::Value (no preserve_order) keeps keys sorted —
            // exactly json.dumps(sort_keys=True).
            out.push('{');
            for (i, (k, val)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                dump_string(k, out);
                out.push_str(": ");
                dump_value(val, out);
            }
            out.push('}');
        }
    }
}

fn dump_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            // CPython's ensure_ascii escapes every code point outside
            // space..tilde — control chars <0x20 AND DEL (0x7f).
            c if (c as u32) < 0x20 || (c as u32) == 0x7f => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) < 0x7f => out.push(c),
            c => {
                // ensure_ascii=True: every non-ASCII code point as \uXXXX
                // (lowercase), astral points as a UTF-16 surrogate pair.
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{:04x}", unit));
                }
            }
        }
    }
    out.push('"');
}

fn digest(kind: &str, data: &[u8]) -> String {
    let mut hasher = Blake2b::<U16>::new();
    hasher.update(DOMAIN);
    hasher.update(kind.as_bytes());
    hasher.update(b"\x00");
    hasher.update(data);
    hex::encode(hasher.finalize())
}

/// The content-address of a structured payload. `kind` domain-separates the
/// id space ("event" for Weft events, "cell" for the Weave, "snapshot" for
/// state roots).
pub fn content_id(payload: &Value, kind: &str) -> String {
    digest(kind, &canonical(payload))
}

/// The content-address of raw bytes (an image, a file, an impl).
pub fn blob_id(data: &[u8], kind: &str) -> String {
    digest(kind, data)
}
