//! The reference-vector fold script — a Rust re-run of the exact fixed event
//! script in heartbeat/decima/vectors.py `_fold_vector`, so both the unit
//! tests and decima-verify re-derive the golden `fold` section from first
//! principles rather than reading it as expected output.

use serde_json::{json, Value};

use crate::crypto::Keyring;
use crate::hashing::content_id;
use crate::model;
use crate::weave::Weave;
use crate::weft::{Weft, ASSERT, RETRACT};
use crate::capability;

/// The fixed all-zero master seed of the reference vectors.
pub const MASTER_SEED: [u8; 32] = [0u8; 32];

#[derive(Debug)]
pub struct FoldResult {
    pub author_pid: String,
    pub type_cell_id: String,
    pub parent_cap_id: String,
    pub child_cap_id: String,
    /// {id, verb, lamport, body} per event, in append order.
    pub events: Vec<Value>,
    pub state_root: String,
    pub type_counts: Vec<(String, usize)>,
    pub event_count: usize,
}

/// Re-run vectors.py `_fold_vector`: define_type "note"; assert_content
/// note:1 twice; capability grant pay (budget 100); attenuate with
/// {"budget": 40, "requires_approval": true}; edge child —attenuates→ parent;
/// RETRACT note:1 WITHDRAW. Then fold and project state_root + type_counts.
pub fn run_fold_script(keyring: &Keyring) -> FoldResult {
    let author = keyring_tester_pid(keyring);
    let mut weft = Weft::new(keyring);

    let mut events: Vec<Value> = Vec::new();
    let mut record = |ev: &crate::weft::Event| {
        events.push(json!({
            "id": ev.id,
            "verb": ev.verb,
            "lamport": ev.lamport,
            "body": ev.body,
        }));
    };

    // vectors.py does NOT `record` the define_type event (it stays on the
    // log — event_count is 7 — but is absent from the golden events list).
    let cid_type = model::define_type(&mut weft, &author, "note", None, None).unwrap();
    record(
        &model::assert_content(&mut weft, &author, "note:1", "note", json!({"text": "first", "n": 1}))
            .unwrap(),
    );
    record(
        &model::assert_content(&mut weft, &author, "note:1", "note", json!({"text": "edited", "n": 2}))
            .unwrap(),
    );

    // A capability grant and its downhill attenuation.
    let parent_cap = capability::grant("pay", "pay", json!({"budget": 100}), &author, &author);
    let parent_id = content_id(&json!({"cap": "pay", "v": 1}), "cell");
    record(
        &weft
            .append(
                &author,
                ASSERT,
                json!({"cell": parent_id, "type": "capability", "content": parent_cap}),
                None,
            )
            .unwrap(),
    );
    let child_cap = capability::attenuate(
        &parent_cap,
        &json!({"budget": 40, "requires_approval": true}),
        &parent_id,
        &author,
        &author,
    );
    let child_id = content_id(&json!({"cap": "pay", "v": 1, "att": 1}), "cell");
    record(
        &weft
            .append(
                &author,
                ASSERT,
                json!({"cell": child_id, "type": "capability", "content": child_cap}),
                None,
            )
            .unwrap(),
    );

    record(&model::assert_edge(&mut weft, &author, &child_id, "attenuates", &parent_id).unwrap());
    record(
        &weft
            .append(&author, RETRACT, json!({"cell": "note:1", "mode": "WITHDRAW"}), None)
            .unwrap(),
    );

    let mut woven = Weave::fold(&weft);
    let state_root = woven.state_root();
    let type_counts: Vec<(String, usize)> = ["note", "type", "capability"]
        .into_iter()
        .map(|t| (t.to_string(), woven.of_type(t).len()))
        .collect();

    FoldResult {
        author_pid: author,
        type_cell_id: cid_type,
        parent_cap_id: parent_id,
        child_cap_id: child_id,
        events,
        state_root,
        type_counts,
        event_count: weft.count(),
    }
}

/// vectors.py mints "tester" as a "human" on the keyring; the pid is a pure
/// function of the name, so derive it without mutating the caller's keyring.
fn keyring_tester_pid(keyring: &Keyring) -> String {
    let mut kr = Keyring::new(keyring.master);
    kr.mint("tester", "human").id
}
