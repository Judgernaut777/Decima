//! The extended-vector script (port milestone 2) — a Rust re-run of the exact
//! event script in rust/vectors/generate.py, so both the unit tests and
//! decima-verify re-derive every value in rust/vectors/extended_vectors.json
//! from first principles: SQLite-persisted Weft (stored payload bytes),
//! warm start, and the INVOKE/ATTEST fold.

use std::collections::BTreeMap;

use serde_json::{json, Value};

use crate::capability;
use crate::crypto::Keyring;
use crate::hashing::content_id;
use crate::model;
use crate::weave::Weave;
use crate::weft::{Event, ASSERT, ATTEST, INVOKE};
use crate::weft_db::WeftDb;

#[derive(Debug)]
pub struct ExtendedResult {
    pub author_pid: String,
    pub attester_pid: String,
    pub type_cell_id: String,
    pub parent_cap_id: String,
    pub child_cap_id: String,
    /// {id, verb, lamport, authorized, body} per event, in append order.
    pub events: Vec<Value>,
    /// (seq, exact stored `payload` TEXT bytes) per row, in seq order.
    pub stored_payloads: Vec<(i64, String)>,
    pub head_after: String,
    pub lamport_after: i64,
    pub event_count: usize,
    /// {event, by, cap, args} per folded INVOKE.
    pub invocations: Vec<Value>,
    /// Per-capability invoke tally (sorted by cap id).
    pub invoke_counts: Vec<(String, i64)>,
    /// cell id -> folded attestations ({by, claim, event}), cells sorted.
    pub attestations: BTreeMap<String, Vec<Value>>,
    pub state_root: String,
    /// Warm start: reopen the same DB file, recover head/lamport, re-fold.
    pub warm_head: String,
    pub warm_lamport: i64,
    pub warm_state_root: String,
}

/// A unique temp DB path per invocation (uniqueness only — the path never
/// enters the pinned bytes).
fn temp_db_path() -> std::path::PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let n = COUNTER.fetch_add(1, Ordering::SeqCst);
    std::env::temp_dir().join(format!(
        "decima-ext-{}-{}-{}.db",
        std::process::id(),
        n,
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.subsec_nanos())
            .unwrap_or(0)
    ))
}

fn record(events: &mut Vec<Value>, ev: &Event) {
    events.push(json!({
        "id": ev.id,
        "verb": ev.verb,
        "lamport": ev.lamport,
        "authorized": ev.authorized,
        "body": ev.body,
    }));
}

/// Re-run generate.py's script: TYPE_DEF "note"; two CONTENT versions of
/// note:1; capability grant pay (budget 100); attenuate to budget 40; two
/// INVOKEs through the child cap (pinned nonces); three ATTESTs (one by a
/// second principal). Persist to SQLite, record the stored bytes, fold, then
/// warm-start (reopen) and re-fold.
pub fn run_extended_script(keyring: &Keyring) -> ExtendedResult {
    let mut kr = Keyring::new(keyring.master);
    let author = kr.mint("tester", "human").id;
    let attester = kr.mint("attester", "agent").id;

    let db_path = temp_db_path();
    let mut weft = WeftDb::open(&db_path, keyring).expect("open weft db");

    let mut events: Vec<Value> = Vec::new();

    let cid_type = model::define_type_db(&mut weft, &author, "note").unwrap();
    record(
        &mut events,
        &weft
            .append(
                &author,
                ASSERT,
                json!({"cell": "note:1", "type": "note", "kind": "CONTENT", "content": {"text": "milestone two", "n": 1}}),
                None,
            )
            .unwrap(),
    );
    record(
        &mut events,
        &weft
            .append(
                &author,
                ASSERT,
                json!({"cell": "note:1", "type": "note", "kind": "CONTENT", "content": {"text": "attested note", "n": 2}}),
                None,
            )
            .unwrap(),
    );

    let parent_cap = capability::grant("pay", "pay", json!({"budget": 100}), &author, &author);
    let parent_id = content_id(&json!({"cap": "pay", "v": 1}), "cell");
    record(
        &mut events,
        &weft
            .append(
                &author,
                ASSERT,
                json!({"cell": parent_id, "type": "capability", "content": parent_cap}),
                None,
            )
            .unwrap(),
    );
    let child_cap =
        capability::attenuate(&parent_cap, &json!({"budget": 40}), &parent_id, &author, &author);
    let child_id = content_id(&json!({"cap": "pay", "v": 1, "att": 1}), "cell");
    record(
        &mut events,
        &weft
            .append(
                &author,
                ASSERT,
                json!({"cell": child_id, "type": "capability", "content": child_cap}),
                None,
            )
            .unwrap(),
    );

    record(
        &mut events,
        &weft
            .append(
                &author,
                INVOKE,
                json!({"cap": child_id, "args": {"amount": 10, "cost": 10},
                       "nonce": "decima-ext-nonce-1"}),
                Some(&child_id),
            )
            .unwrap(),
    );
    record(
        &mut events,
        &weft
            .append(
                &author,
                INVOKE,
                json!({"cap": child_id, "args": {"amount": 5, "cost": 5},
                       "nonce": "decima-ext-nonce-2"}),
                Some(&child_id),
            )
            .unwrap(),
    );

    record(
        &mut events,
        &weft
            .append(
                &author,
                ATTEST,
                json!({"target_cell": "note:1", "claim": "verified by author"}),
                None,
            )
            .unwrap(),
    );
    record(
        &mut events,
        &weft
            .append(
                &attester,
                ATTEST,
                json!({"target_cell": "note:1", "claim": "witnessed"}),
                None,
            )
            .unwrap(),
    );
    record(
        &mut events,
        &weft
            .append(
                &author,
                ATTEST,
                json!({"target_cell": child_id, "claim": "cap review ok"}),
                None,
            )
            .unwrap(),
    );

    let stored_payloads = weft.stored_payloads().unwrap();
    let head_after = weft.head().unwrap().to_string();
    let lamport_after = weft.lamport();
    let event_count = weft.count().unwrap();

    let first_events = weft.events().expect("verified read");
    let mut first = Weave::fold_events(first_events.iter());
    let state_root = first.state_root();

    let invocations: Vec<Value> = first
        .invocations
        .iter()
        .map(|i| json!({"event": i.event, "by": i.by, "cap": i.cap, "args": i.args}))
        .collect();
    let mut invoke_counts: Vec<(String, i64)> =
        first.invoke_counts.iter().map(|(k, v)| (k.clone(), *v)).collect();
    invoke_counts.sort();
    let mut attestations: BTreeMap<String, Vec<Value>> = BTreeMap::new();
    for (cid, cell) in &first.cells {
        if !cell.attestations.is_empty() {
            attestations.insert(cid.clone(), cell.attestations.clone());
        }
    }
    drop(weft);

    // Warm start: reopen the SAME file — head/lamport recovered via
    // _load_head semantics — and re-fold from the verified on-read stream.
    let warm = WeftDb::open(&db_path, keyring).expect("reopen weft db");
    let warm_head = warm.head().unwrap().to_string();
    let warm_lamport = warm.lamport();
    let warm_events = warm.events().expect("verified warm read");
    let mut warm_woven = Weave::fold_events(warm_events.iter());
    let warm_state_root = warm_woven.state_root();
    let _ = std::fs::remove_file(&db_path);

    ExtendedResult {
        author_pid: author,
        attester_pid: attester,
        type_cell_id: cid_type,
        parent_cap_id: parent_id,
        child_cap_id: child_id,
        events,
        stored_payloads,
        head_after,
        lamport_after,
        event_count,
        invocations,
        invoke_counts,
        attestations,
        state_root,
        warm_head,
        warm_lamport,
        warm_state_root,
    }
}
