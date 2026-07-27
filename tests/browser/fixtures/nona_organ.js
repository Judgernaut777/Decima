// Fixture content for the self-extension (Nona) browser spec.
//
// The candidate source is HOSTILE ON PURPOSE. An extension candidate's implementation is
// generated code: on the log it is quarantined DATA (`source_is_data: True`) and in the
// browser it is quarantined DATA too. So the fixture organ carries, inside ordinary Python
// string literals, every payload that would execute if the Shell ever put candidate bytes
// into markup — a closing </pre> to break out of the container, a <script>, an inline
// onerror handler, and a javascript: URL. The spec then asserts that the page shows those
// bytes as TEXT, that no dialog fires, and that no script/img element was constructed
// inside the untrusted zone.
//
// The Python itself stays boring and honest: `main(x) -> x + 1`, no imports, no dynamic
// execution (the Reckoner's static scan makes `eval`/`exec`/an undeclared import a HIGH
// finding, and a HIGH finding is what SHOULD block a promotion — this candidate is meant to
// clear the gate so the promote/rollback surface can be driven).

// The marker every payload tries to surface. If it ever reaches a dialog or a real element,
// the escaping discipline broke.
const XSS_MARKER = "nona-xss-marker";

// Rendered as text by the screen; asserted verbatim in the spec.
const HOSTILE_HTML = '</pre><script>alert("' + XSS_MARKER + '")</script>';

const ORGAN_SOURCE = [
  "def main(x):",
  '    """Add one.',
  "",
  "    Docstring payload (must render as text, never as markup):",
  "    " + HOSTILE_HTML,
  '    <img src=x onerror=alert("' + XSS_MARKER + '")>',
  '    <a href="javascript:alert(1)">click</a>',
  '    """',
  // Single-quoted on the Python side: the payload itself contains double quotes, and the
  // fixture must PARSE (the Reckoner's static scan makes unparseable source a HIGH finding,
  // and this candidate is meant to clear the gate).
  "    breakout = '" + HOSTILE_HTML + "'",
  "    assert breakout",
  "    return int(x) + 1",
  "",
].join("\n");

// Baseline cases, typed into the candidate card's JSON field. They are BASELINE input from
// the operator; the adversarial cases the organ is judged by come from the lane's root-side
// constant and cannot be supplied from the browser (design Decision 6).
const ORGAN_CASES = '[{"in": {"x": 1}, "out": 2}, {"in": {"x": 41}, "out": 42}]';

// A second candidate at the `network` tier: authorable, evaluatable, and NEVER promotable,
// because no networked executor exists. The spec asserts the screen says exactly that
// instead of offering an approval (design Decision 2).
const NETWORK_ORGAN_SOURCE = ["def main(x):", "    return int(x) + 1", ""].join("\n");

module.exports = {
  XSS_MARKER,
  HOSTILE_HTML,
  ORGAN_SOURCE,
  ORGAN_CASES,
  NETWORK_ORGAN_SOURCE,
};
