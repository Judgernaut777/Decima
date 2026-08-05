//! Capabilities — Law 2: no ambient authority. A capability is a Cell.
//!
//! Port of heartbeat/decima/capability.py, scoped to capability construction
//! and downhill attenuation (what the reference vector script exercises).
//! Delegation-chain verification, leases, and authorization are not ported.

use serde_json::{json, Map, Value};

/// Lease caveats are numeric BOUNDS that may only shrink under attenuation.
const SHRINK_ONLY: [&str; 3] = ["budget", "expires_at", "max_uses"];

#[allow(clippy::too_many_arguments)]
pub fn capability_content(
    name: &str,
    effect: &str,
    target: &str,
    caveats: Value,
    delegable: bool,
    impl_: Value,
    quarantined: bool,
    parent: Option<&str>,
    grantee: Option<&str>,
    granter: Option<&str>,
) -> Value {
    json!({
        "name": name,
        "effect": effect,
        "target": target,
        "caveats": caveats,
        "delegable": delegable,
        "impl": impl_,
        "quarantined": quarantined,
        "parent": parent,
        "grantee": grantee,
        "granter": granter,
    })
}

/// Convenience matching capability.capability_content's defaults
/// (target="*", caveats={}, delegable=True, impl=None, quarantined=False).
pub fn grant(name: &str, effect: &str, caveats: Value, grantee: &str, granter: &str) -> Value {
    capability_content(
        name,
        effect,
        "*",
        caveats,
        true,
        Value::Null,
        false,
        None,
        Some(grantee),
        Some(granter),
    )
}

/// Derive a weaker capability granted to `grantee` by `granter`. Caveats can
/// only get tighter (downhill): numeric bounds may only shrink, and adding a
/// constraint only narrows.
pub fn attenuate(
    parent_content: &Value,
    stricter: &Value,
    parent_id: &str,
    grantee: &str,
    granter: &str,
) -> Value {
    let mut caveats = parent_content
        .get("caveats")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_else(Map::new);
    if let Value::Object(stricter) = stricter {
        for (k, v) in stricter {
            if SHRINK_ONLY.contains(&k.as_str()) {
                // min(int(v), int(caveats.get(k, v))) — ints only
                let new = v.as_i64().expect("shrink-only caveat must be an int");
                let cur = caveats.get(k).and_then(Value::as_i64).unwrap_or(new);
                caveats.insert(k.clone(), json!(new.min(cur)));
            } else {
                caveats.insert(k.clone(), v.clone());
            }
        }
    }
    capability_content(
        parent_content["name"].as_str().expect("cap name"),
        parent_content["effect"].as_str().expect("cap effect"),
        parent_content["target"].as_str().expect("cap target"),
        Value::Object(caveats),
        parent_content["delegable"].as_bool().unwrap_or(true),
        parent_content.get("impl").cloned().unwrap_or(Value::Null),
        parent_content
            .get("quarantined")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        Some(parent_id),
        Some(grantee),
        Some(granter),
    )
}
