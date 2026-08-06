//! The thin domain model — types and edges as DATA, not kernel code.
//!
//! Port of heartbeat/decima/model.py: the three WEFT §4 assertion kinds as
//! helpers over `weft.append` (the fold dispatches on the body's `kind`).

use serde_json::{json, Value};

use crate::hashing::{content_id, nfc};
use crate::weft::{Event, Weft, WeftError, ASSERT};

/// Register a type as a Cell and return its id. Idempotent by content: the
/// same type name always lands on the same TYPE_DEF cell id (content-addressed
/// by NAME only).
pub fn define_type(
    weft: &mut Weft,
    author: &str,
    name: &str,
    merge_class: Option<&str>,
    field_classes: Option<&Value>,
) -> Result<String, WeftError> {
    let cid = content_id(&json!({"type_def": name}), "cell");
    let mut content = json!({"name": name});
    if let Some(mc) = merge_class {
        content["merge_class"] = json!(mc);
    }
    if let Some(fc) = field_classes {
        content["field_classes"] = fc.clone();
    }
    weft.append(
        author,
        ASSERT,
        json!({"cell": cid, "type": "type", "kind": "TYPE_DEF", "content": content}),
        None,
    )?;
    Ok(cid)
}

/// `define_type` over the SQLite-persisted Weft (same body shape).
pub fn define_type_db(
    weft: &mut crate::weft_db::WeftDb,
    author: &str,
    name: &str,
    merge_class: Option<&str>,
) -> Result<String, WeftError> {
    let cid = content_id(&json!({"type_def": name}), "cell");
    let mut content = json!({"name": name});
    if let Some(mc) = merge_class {
        content["merge_class"] = json!(mc);
    }
    weft.append(
        author,
        ASSERT,
        json!({"cell": cid, "type": "type", "kind": "TYPE_DEF", "content": content}),
        None,
    )?;
    Ok(cid)
}

/// Assert a CONTENT version of a Cell.
pub fn assert_content(
    weft: &mut Weft,
    author: &str,
    cell: &str,
    r#type: &str,
    content: Value,
) -> Result<Event, WeftError> {
    weft.append(
        author,
        ASSERT,
        json!({"cell": cell, "type": r#type, "kind": "CONTENT", "content": content}),
        None,
    )
}

/// Assert a typed relation `src → rel → dst`. The edge has no cell of its
/// own; the fold folds it onto src.edges_out and dst.edges_in.
pub fn assert_edge(
    weft: &mut Weft,
    author: &str,
    src: &str,
    rel: &str,
    dst: &str,
) -> Result<Event, WeftError> {
    weft.append(
        author,
        ASSERT,
        json!({"kind": "EDGE", "src": src, "rel": nfc(rel), "dst": dst}),
        None,
    )
}
