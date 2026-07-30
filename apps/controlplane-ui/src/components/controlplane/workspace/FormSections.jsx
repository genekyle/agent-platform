import { AppIcon } from "../../../ui/Icon";

// THE ACCORDION CARD — what an apply form is hiding, and the one press that opens it.
//
// This exists because of a failure that produces no error. SAP's candidate profile is nine
// collapsed section bars, and a collapsed section's fields are ABSENT FROM THE AX SCAN — not
// disabled, not empty, absent. So a fill plan over a shut form reports "0 of 0 fields" with
// complete confidence, and the operator reads that as "nothing to do here" rather than "nobody
// opened the form". Every number downstream is accurate and every one of them is about the wrong
// page.
//
// So the card's whole job is to make CLOSED look different from EMPTY. It renders even when the
// form has nothing in it, because that is exactly the case it was built for.
//
// Three states per bar, and the third one matters:
//   open     — aria-expanded=true, its fields are in the scan
//   closed   — aria-expanded=false, its fields are not
//   unknown  — the bar was not on the page, or never claimed to be expandable. NOT folded into
//              "closed": a bar we could not read is not a bar we know is shut, and flattening
//              the two is how a page we cannot see becomes a page we believe is fine.

// The marks mirror the page's OWN chevrons (down = open, right = shut), so the card and the
// window read the same way round without translation.
const STATE_TONE = { open: "ready", closed: "warn", unknown: "muted" };
const STATE_MARK = { open: "chevronDown", closed: "chevronRight", unknown: "alert" };

// The field key -> something a person reads. The live label is preferred when we have it, because
// it carries what the page actually says — including the count in "Jobs Applied (2)", which is the
// cheapest read of how many applications this tenant thinks we have sent.
function labelFor(row) {
  if (row.label) return row.label;
  return (row.field || "").replace(/^profile_section_/, "").replace(/_/g, " ");
}

export default function FormSections({ sections, busy, onExpand }) {
  if (!sections) return null;
  const rows = sections.sections || [];
  if (!rows.length) return null;

  const nClosed = (sections.closed || []).length;
  const nUnknown = (sections.unknown || []).length;

  return (
    <div className="sc-login">
      <div className="sc-login__head">
        <AppIcon name="layers" size={14} /> Form sections
        <span className={`badge badge--${sections.all_open ? "ready" : "warn"}`}>
          {sections.all_open ? "all open" : `${nClosed} closed`}
        </span>
        {sections.page && <span className="rung__meta">{sections.page}</span>}
      </div>

      {/* The sentence that has to be here. Without it the panel shows an empty field list beside
          a closed accordion and says nothing about the connection between the two. */}
      {nClosed > 0 && (
        <p className="cv-blocked">
          A closed section's fields are not on the page at all — they are absent from the scan,
          not merely unfilled. Anything read or filled right now describes only what is open.
        </p>
      )}
      {nUnknown > 0 && (
        <p className="rung__meta">
          {nUnknown} bar{nUnknown === 1 ? "" : "s"} could not be read on this page — that is not
          the same as closed, and it usually means the tab is not on the profile.
        </p>
      )}

      <ul className="rungs">
        {rows.map((r) => (
          <li key={r.field} className={`rung rung--${r.state === "open" ? "held" : "pending"}`}>
            <span className={`rung__mark badge badge--${STATE_TONE[r.state] || "muted"}`}>
              <AppIcon name={STATE_MARK[r.state] || "circle"} size={13} />
            </span>
            <div className="rung__body">
              <div className="rung__line">
                <span className="rung__label">{labelFor(r)}</span>
                <span className={`badge badge--${STATE_TONE[r.state] || "muted"}`}>{r.state}</span>
              </div>
            </div>
            {/* Per-section open, because "expand all" is nine clicks the site sees at once and a
                targeted drive usually wants exactly one section. Absent for a bar we cannot read:
                offering to click something we could not find is how a no-op reports success. */}
            {r.state === "closed" && (
              <button className="btn btn-sm btn-ghost" disabled={busy}
                      title={`Open just this section, then re-read the page`}
                      onClick={() => onExpand(r.field)}>
                Open
              </button>
            )}
          </li>
        ))}
      </ul>

      <div className="cv-actions">
        <button className="btn btn-sm btn-primary" disabled={busy || sections.all_open}
                title="Clicks the form's own 'Expand all sections' control, then re-reads to confirm the bars actually opened"
                onClick={() => onExpand("all")}>
          Open all sections
        </button>
        <button className="btn btn-sm" disabled={busy}
                title="Re-read the bars without touching anything"
                onClick={() => onExpand(null)}>
          Re-read
        </button>
      </div>
    </div>
  );
}
