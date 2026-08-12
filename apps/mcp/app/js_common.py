"""Page-side tells, defined ONCE and injected into every JS block that needs them.

Why this file exists — and it is not a hypothetical:

`DESCRIBE_WIDGET_JS` and `SCAN_REQUIRED_JS` were written an hour apart in the same session
and disagreed about how to recognise a react-select. describe_widget checked
`[class*=select__control]` (right); scan_required checked `[class*=singleValue]` (wrong).
That is the identical failure the shared `interaction` package exists to prevent — the
autofill matcher drifting from its Python twin — reproduced inside one session, in one app,
by one author. Copies drift. These are the tells; they live here.

THE TELL THAT MATTERS. `[class*=singleValue]` DOES NOT EXIST until the widget is answered —
react-select renders a placeholder when empty and only mounts singleValue once a value is
picked. So detecting a react-select by singleValue works on exactly the fields you don't
care about and fails on exactly the fields you do (the unanswered ones). Verified live on
KKR's Greenhouse form 2026-07-16:

    #country / #school--0 / #degree--0 (all unanswered):
        singleValue=False  select__control=True  aria-autocomplete=list  role=combobox
    #first_name / #start-date-year-0 (plain inputs):
        singleValue=False  select__control=False aria-autocomplete=None  role=None

That mattered for more than tidiness. `scan_required` fell through to `.value` for every
react-select it returned, and **react-select's `.value` holds TRANSIENT SEARCH TEXT** — so a
half-typed field reads as ANSWERED, drops out of the unanswered list, and the form-complete
gate passes an incomplete form. It was latent only because nothing had typed into those
fields yet.
"""

from __future__ import annotations

#: Prepend to the body of any page-side arrow function. Defines:
#:   __vis(el)        — is it really on screen (offsetParent + a non-zero rect)
#:   __txt(el)        — normalized visible text
#:   __isReactSelect(el)
#:   __valueTruth(el) — {read_at, answered, preview}: WHERE this widget's truth lives and
#:                      what it currently says. The one function that owns the `.value` lie.
WIDGET_TELLS_JS = r"""
  const __vis = (n) => { try { const r = n.getBoundingClientRect();
                               return n.offsetParent !== null && r.width > 0 && r.height > 0; }
                         catch (e) { return false; } };
  const __txt = (n) => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const __attr = (n, a) => (n && n.getAttribute ? n.getAttribute(a) : null);

  // A control a HUMAN could actually fill: on screen AND keyboard-reachable.
  //
  // `tabIndex === -1` is the tell, and it is doing real work: Greenhouse's react-select
  // mounts a hidden proxy input (`class*=requiredInput`, tabIndex=-1, opacity:0) purely so
  // native form validation fires. It is `required`, it is unanswered, and __vis() says it is
  // VISIBLE — offsetParent is non-null and it has a 608x22 rect, because it is hidden with
  // OPACITY, not display:none. So it sails through a rect check and shows up as a phantom
  // required question. This is the "hidden required twin" GREENHOUSE_LESSONS warns about
  // ("a duplicate empty-id field is NOT a second question").
  //
  // Do NOT reach for Element.checkVisibility({opacityProperty:true}) here, which looks like
  // the obvious fix. Measured live on KKR 2026-07-16: it rejects the proxy (good) AND
  // rejects #country and #school--0 (fatal) — react-select's own search input is opacity:0
  // whenever the singleValue is showing, so an opacity-aware check drops every react-select
  // from the scan. Opacity does not separate them. tabIndex does: 1 proxy at -1, all 29 real
  // fields at 0.
  const __isUserField = (el) => __vis(el) && el.tabIndex !== -1;

  // The page's own verdict on a value it already holds. A required field can be FILLED and
  // still block: form_complete_gate treats `satisfied = (not required) or (filled and valid)`,
  // so "filled but invalid" is a distinct blocker from "empty" and must not be lost.
  const __invalid = (el) => {
    if (!el) return false;
    if (__attr(el, 'aria-invalid') === 'true') return true;
    try { return typeof el.matches === 'function' && el.matches(':invalid'); }
    catch (e) { return false; }
  };

  // A react-select, whether or not it has been answered. Order matters: select__control is
  // the structural tell and is present when empty; aria-autocomplete=list is the ARIA tell
  // and catches skinned variants that rename the class.
  const __isReactSelect = (el) => {
    if (!el) return false;
    if (el.closest('[class*=select__control], [class*=select__value-container]')) return true;
    return __attr(el, 'aria-autocomplete') === 'list' && __attr(el, 'role') === 'combobox';
  };

  // WHERE this widget's truth lives, and what it says right now.
  // NEVER read a react-select at .value: that holds the transient search text, empties on
  // blur, and reports a half-typed field as answered.
  const __valueTruth = (el) => {
    if (!el) return {read_at: '', answered: false, preview: ''};
    if (el.tagName === 'INPUT' && el.type === 'file')
      return {read_at: 'files.length', answered: !!(el.files && el.files.length),
              preview: (el.files && el.files.length) ? el.files[0].name : ''};
    if (__isReactSelect(el)) {
      // Scope to the widget's OWN control, not an ancestor that may wrap several fields.
      const ctl = el.closest('[class*=select__control]') ||
                  el.closest('[class*=field], div') || el.parentElement;
      const sv = ctl && ctl.querySelector('[class*=singleValue]');
      const multi = ctl && ctl.querySelectorAll('[class*=multiValue]');
      const t = sv ? __txt(sv) : (multi && multi.length ? __txt(ctl) : '');
      return {read_at: '[class*=singleValue]', answered: !!t, preview: t.slice(0, 40)};
    }
    if (el.tagName === 'SELECT')
      return {read_at: '.value', answered: el.selectedIndex > 0 && el.value !== '',
              preview: (el.value || '').slice(0, 40)};
    // WORKDAY'S MULTISELECT — the third shape of the `.value` lie, same family as react-select.
    // The node the scan holds is a SEARCH box inside `multiselectInputContainer`; the answer is
    // the selected-item pill beside it, and the container's own aria-label states it outright
    // ("1 item selected, United States of America (+1)"). Reading `.value` called an answered
    // Country Phone Code unanswered and blocked a Continue over a field the page considered
    // done (live 2026-08-11, SolutionHealth's Workday). The container's label is the truth the
    // widget publishes about itself, so that is what we read.
    const __ms = el.closest ? el.closest('[data-automation-id=multiselectInputContainer]') : null;
    if (__ms) {
      const aria = __attr(__ms, 'aria-label') || '';
      const m = aria.match(/^\s*(\d+)\s+items?\s+selected\s*,?\s*(.*)$/i);
      const chosen = m ? (m[2] || '').trim() : '';
      const n = m ? parseInt(m[1], 10) : 0;
      // Fall back to the pills when the container states no count (older tenants).
      const pill = chosen || __txt(__ms.querySelector('[data-automation-id*=selectedItem]') || null);
      return {read_at: 'multiselect_selected_items', answered: n > 0 || !!pill,
              preview: (pill || '').slice(0, 40)};
    }
    // An ARIA combobox/listbox OPENER — a div with no .value anywhere. Its truth is its own
    // visible label (the widget protocol's `opener_label`). The generic fallback below read
    // el.value (undefined on a div), so smartapply's question dropdowns scanned as unanswered
    // FOREVER, even freshly committed (live, 2026-08-10). Placeholder text still counts as
    // UNANSWERED on purpose: the dangerous mistake is a placeholder passing form_complete_gate,
    // not a filled field being asked about twice.
    const __role = el.getAttribute ? (el.getAttribute('role') || '') : '';
    if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA' &&
        (__role === 'combobox' ||
         (el.getAttribute && el.getAttribute('aria-haspopup') === 'listbox'))) {
      const t = __txt(el).trim();
      const ph = /^(select|choose)( an?| your)? (option|answer|one)\b/i.test(t) ||
                 /^(select|choose)(\.{3}|…)?$/i.test(t);
      return {read_at: 'opener_label', answered: !!t && !ph, preview: t.slice(0, 40)};
    }
    const v = String(el.value == null ? '' : el.value);
    return {read_at: '.value', answered: !!v.trim(), preview: v.slice(0, 40)};
  };
"""
