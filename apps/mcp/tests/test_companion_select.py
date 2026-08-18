"""One field, one answer — whichever endpoint asks.

THE INCIDENT. Live 2026-08-14, BrassRing (Boston Children's, Questions step), on
`#custom_10112_465_fname_slt_0_10112-input`:

    /scan_required   -> answered: True,  value: "LinkedIn", value_read_at: "companion_select"
    /describe_widget -> answered: False, value_preview: "", value_read_at: "[class*=singleValue]"

Same node, same page, same instant. React-select renders `id="X-input"` over a real `<select
id="X">` holding the choice (its own `inputId` convention), and this particular rendering never
mounts a `[class*=singleValue]` — so the rendered witness is empty while the native witness is
correct. The census resolved the twin; the classifier did not, because it had its own hand-rolled
copy of "where does this widget's truth live".

WHY A FALSE "UNANSWERED" IS WORSE THAN A WRONG VALUE. It invites a retry, and a retry on a
react-select is not idempotent — docs/LEARNINGS.md 2026-08-13 records a retry that reopened the
widget and set the WRONG question. So `answered` must not depend on who asked.

--------------------------------------------------------------------------------------
WHAT THESE TESTS CAN AND CANNOT DO, stated plainly
--------------------------------------------------------------------------------------
`test_describe_widget.py` explains why jsdom is not used here: its `offsetParent` is always null,
the classifier's visibility helper is built on it, and every assertion would be validating a
fiction. That objection holds for `DESCRIBE_WIDGET_JS` as a whole, which needs a real selector
engine and real layout.

It does NOT hold for `__valueTruth`, which touches no layout at all — so the value decision is
executed for real, against the shipped JS text, on a DOM small enough to read (below). The half
that cannot be executed offline — "and the classifier asks that same function" — is enforced the
way this repo already enforces the shared tells (see test_js_blob_tells.py): by scanning the
blobs as text, so a new copy cannot be written without failing.
"""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.js_common import WIDGET_TELLS_JS
from app.protocols import SCAN_REQUIRED_JS
from app.widget_probe import DESCRIBE_WIDGET_JS

# --------------------------------------------------------------------------- the executed half

#: A DOM with exactly the surface `__valueTruth` touches: tag, id, class, attributes, parentage,
#: text, and a <select>'s selected option. No layout, no visibility, nothing stubbed that the
#: function under test actually consults. The selector matcher understands only the three forms
#: the tells use — `[class*=x]`, `[attr=value]`, and a bare tag name, comma-separated.
_DOM_JS = r"""
class N {
  constructor(tag, attrs = {}, kids = []) {
    this.tagName = tag.toUpperCase();
    this._a = attrs;
    this.id = attrs.id || '';
    this.className = attrs.class || '';
    this.type = attrs.type || '';
    this.value = attrs.value === undefined ? '' : attrs.value;
    this.children = kids;
    this.parentElement = null;
    this._text = attrs.text || '';
    for (const k of kids) k.parentElement = this;
  }
  get ownerDocument() { return document; }
  get textContent() { return this._text + this.children.map(k => k.textContent).join(''); }
  get innerText() { return this.textContent; }
  get text() { return this._text; }                       // an <option>'s words
  get selectedOptions() {
    return this.children.filter(k => k._a.selected);
  }
  getAttribute(k) { return k in this._a ? String(this._a[k]) : null; }
  get descendants() { return this.children.flatMap(k => [k, ...k.descendants]); }
  matches(sel) {
    return sel.split(',').some(one => {
      const s = one.trim();
      let m = s.match(/^\[class\*=([^\]]+)\]$/);
      if (m) return (this.className || '').includes(m[1]);
      m = s.match(/^\[([\w-]+)=([^\]]+)\]$/);
      if (m) return this.getAttribute(m[1]) === m[2];
      m = s.match(/^\[([\w-]+)\]$/);
      if (m) return this.getAttribute(m[1]) !== null;
      return this.tagName === s.toUpperCase();
    });
  }
  closest(sel) {
    for (let n = this; n; n = n.parentElement) if (n.matches(sel)) return n;
    return null;
  }
  querySelectorAll(sel) { return this.descendants.filter(n => n.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

// THE MEASURED SHAPE (BrassRing, Boston Children's, 2026-08-14). The react combobox sits inside
// a select__control that never mounted a singleValue; the real answer is in a sibling <select>
// whose id is the input's id minus '-input'.
const ID = 'custom_10112_465_fname_slt_0_10112';
const build = ({singleValue = null, selected = 'LinkedIn', selectedValue = 'ILIN_Self'} = {}) => {
  const input = new N('input', {id: ID + '-input', role: 'combobox',
                                'aria-autocomplete': 'list'});
  const kids = [new N('div', {class: 'select__value-container'}, [input])];
  if (singleValue !== null) kids.push(new N('div', {class: 'css-1x singleValue', text: singleValue}));
  const control = new N('div', {class: 'css-9y select__control'}, kids);
  const options = [new N('option', {value: '', text: 'Select', selected: selected === null})];
  if (selected !== null)
    options.push(new N('option', {value: selectedValue, text: selected, selected: true}));
  const native = new N('select', {id: ID, value: selected === null ? '' : selectedValue}, options);
  const root = new N('div', {class: 'form'}, [control, native]);
  return {input, root};
};

const byId = {};
const document = {getElementById: (i) => byId[i] || null};

const run = (opts) => {
  const {input, root} = build(opts);
  for (const n of [root, ...root.descendants]) if (n.id) byId[n.id] = n;
  const out = (() => {
    __TELLS__
    return __valueTruth(input);
  })();
  for (const k of Object.keys(byId)) delete byId[k];
  return out;
};
"""


def _node_eval(cases: dict[str, dict]) -> dict[str, dict]:
    """Run `__valueTruth` on each case in node and return its verdicts."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH — the value-truth execution test needs a JS runtime")
    script = (_DOM_JS.replace("__TELLS__", WIDGET_TELLS_JS)
              + "\nconst CASES = " + json.dumps(cases) + ";"
              + "\nconst out = {};"
              + "\nfor (const [k, v] of Object.entries(CASES)) out[k] = run(v);"
              + "\nconsole.log(JSON.stringify(out));\n")
    r = subprocess.run([node, "--input-type=module", "-e", script],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"the shared tells failed to run in node:\n{r.stderr}"
    return json.loads(r.stdout)


def test_the_shared_helper_reads_the_answer_off_the_hidden_native_twin():
    """The BrassRing field, executed: no singleValue, a native select holding "LinkedIn"."""
    got = _node_eval({"brassring": {}})["brassring"]
    assert got["answered"] is True, (
        "the react-select renders no singleValue, so the only witness to 'LinkedIn' is the "
        "companion <select> — reading answered=False here is the false blocker that stops an "
        "advance gate over a form the page itself considers complete"
    )
    assert got["read_at"] == "companion_select"
    assert got["preview"] == "LinkedIn"


def test_a_companion_holding_no_selection_is_still_unanswered():
    """The helper must not turn "there is a twin" into "there is an answer" — an over-claim here
    is the dangerous direction: a placeholder passing form_complete_gate."""
    got = _node_eval({"empty": {"selected": None}})["empty"]
    assert got["answered"] is False
    assert got["read_at"] == "[class*=singleValue]"


def test_a_mounted_singlevalue_still_wins_so_nothing_working_changes():
    """The twin is consulted ONLY when the rendered witness is empty."""
    got = _node_eval({"rendered": {"singleValue": "Indeed"}})["rendered"]
    assert got["answered"] is True
    assert got["read_at"] == "[class*=singleValue]" and got["preview"] == "Indeed"


# ------------------------------------------------------------------------ the structural half

def test_both_endpoints_decide_answered_with_the_same_function():
    """`/scan_required` and `/describe_widget` must reach the SAME `__valueTruth`.

    This is the assertion the incident is about: the two endpoints disagreed because the
    classifier had its own copy of the value read. Executing both blobs offline would need a real
    DOM (see the module docstring), so the enforcement is textual — but it is exact, because
    after the fix neither blob decides `answered` for a single control on its own.
    """
    # The tells reach both blobs at all. Necessary, not sufficient: `__valueTruth(el)` appears
    # INSIDE the tells (`__questionOf` consults it), so looking for that call in the injected
    # blob would pass for a blob that never asks it — which is exactly the pre-fix classifier.
    for name, blob in (("DESCRIBE_WIDGET_JS", DESCRIBE_WIDGET_JS),
                       ("SCAN_REQUIRED_JS", SCAN_REQUIRED_JS)):
        assert "const __companionSelect" in blob, (
            f"{name} does not carry the companion resolver — check the __WIDGET_TELLS__ injection."
        )

    # So the call is looked for in each endpoint's OWN source, before injection.
    from app import protocols, widget_probe

    for module in (protocols, widget_probe):
        src = module.__loader__.get_source(module.__name__)
        assert "__valueTruth(el)" in src, (
            f"{module.__name__} no longer asks __valueTruth where this widget's truth lives. "
            f"That is how the same field read ANSWERED to /scan_required and UNANSWERED to "
            f"/describe_widget on the same page (live 2026-08-14, BrassRing)."
        )

    # And the classifier's own singleValue read is GONE, not merely supplemented: a local read
    # left beside the shared one is a second answer waiting to be preferred.
    probe_src = widget_probe.__loader__.get_source("app.widget_probe")
    assert "querySelector('[class*=singleValue]')" not in probe_src, (
        "/describe_widget is reading singleValue itself again — ask __valueTruth instead"
    )
    assert "answered = truth.answered" in probe_src


def test_the_id_convention_is_written_down_exactly_once():
    """`<select id>` == `<input id>` minus `-input` is the rule that resolved this live. A second
    copy of it is a second thing to keep in step, which is the whole reason js_common.py exists."""
    from app import js_common, main_server, protocols, widget_probe

    sources = {m.__name__: m.__loader__.get_source(m.__name__)
               for m in (js_common, protocols, widget_probe, main_server)}
    definers = [name for name, src in sources.items() if "const __companionSelect" in src]
    assert definers == ["app.js_common"], (
        f"__companionSelect is defined in {definers} — it belongs in app.js_common alone"
    )
    # Match the RULE, not the bare suffix: Workday's segmented date builds its own
    # `-dateSectionMonth-input` id in main_server, and that is a different convention that
    # happens to end the same way. A substring test conflates them and fails on the innocent one.
    conventioneers = [name for name, src in sources.items() if "slice(-6) === '-input'" in src]
    assert conventioneers == ["app.js_common"], (
        f"the '-input' id convention is spelled out in {conventioneers}; copies drift"
    )
