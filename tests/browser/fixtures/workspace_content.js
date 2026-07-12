// workspace_content.js — deterministic inputs the Scenario C spec types into the
// visible workspace form. The fixture repo lives beside this file under
// workspace_repo/; the corrected calc.py is what the operator pastes as the bounded
// edit so the declared python_tests check turns from failing to passing.

const path = require("path");

// Absolute path to the committed deterministic fixture repository.
const WORKSPACE_REPO = path.resolve(__dirname, "workspace_repo");

// The bounded change: a correct add() so both fixture tests pass.
const FIXED_CALC =
  "def add(a, b):\n" +
  "    return a + b\n" +
  "\n" +
  "\n" +
  "def mul(a, b):\n" +
  "    return a * b\n";

module.exports = { WORKSPACE_REPO, FIXED_CALC };
