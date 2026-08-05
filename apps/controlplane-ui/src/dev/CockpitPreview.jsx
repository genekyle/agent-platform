import { useEffect, useMemo, useRef, useState } from "react";
import { deriveCockpit } from "../components/controlplane/workspace/cockpit/lifecycle";
import { SessionRail } from "../components/controlplane/workspace/cockpit/SessionRail";
import { WorkSurface } from "../components/controlplane/workspace/cockpit/WorkSurface";
import { DecisionInspector } from "../components/controlplane/workspace/cockpit/DecisionInspector";
import { FIXTURES } from "./cockpitFixtures";
import { AppIcon } from "../ui/Icon";
import "../components/controlplane/workspace/cockpit/cockpit.css";

// A dev harness for the session cockpit. Renders the three panes against captured payloads, with no
// API, no browser and no live session — so the states that are hard to reach on purpose (a
// stop-state, an unrecognised page, an empty session) can be looked at whenever, and the layout can
// be checked without spending a drive to get into position.
//
// Reachable at /cockpit-preview.html while the dev server is running. It is not part of the app.

export default function CockpitPreview() {
  const [fixtureId, setFixtureId] = useState(FIXTURES[0].id);
  const [selection, setSelection] = useState(null);
  const [viewMoment, setViewMoment] = useState(null);
  const [picks, setPicks] = useState([]);
  const [note, setNote] = useState("");
  const [form, setForm] = useState({ query: "", location: "", radius_miles: 50 });
  const [log, setLog] = useState([]);

  const fixture = FIXTURES.find((f) => f.id === fixtureId);
  const panel = fixture.panel;
  const cockpit = useMemo(() => deriveCockpit(panel, { picks }), [panel, picks]);

  // Nothing here calls the API. A press is recorded so the harness can show WHICH intent a control
  // would have sent — the same thing the inspector prints, checked from the other side.
  const call = (path, body) => {
    setLog((l) => [`POST ${path} ${JSON.stringify({ ...body, initiator: "operator" })}`, ...l].slice(0, 6));
    return Promise.resolve(null);
  };

  // THE INVARIANT, MEASURED. Counted from the painted DOM and scoped to the cockpit, so the
  // fixture switcher's own buttons are not part of the screen under test. Written straight to the
  // badge rather than into state: this reads the DOM after paint, which is what an effect is for,
  // and routing it through state would re-render to measure the render.
  const countRef = useRef(null);
  useEffect(() => {
    const n = document.querySelectorAll(".cockpit .btn-primary").length;
    if (!countRef.current) return;
    countRef.current.textContent = `${n} primary on screen`;
    countRef.current.className = `badge badge--${n > 1 ? "warn" : "ready"}`;
  });

  return (
    <div style={{ padding: 16, minHeight: "100vh", background: "var(--canvas)", color: "var(--text)" }}>
      <div className="cockpit-bar">
        <AppIcon name="flask" size={16} />
        <span className="cockpit-bar__id">Cockpit preview</span>
        <span className="cockpit-bar__sub">{fixture.note}</span>
        <span className="cockpit-bar__spacer" />
        <span ref={countRef} className="badge badge--muted">counting…</span>
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {FIXTURES.map((f) => (
          <button key={f.id} className={`btn btn-sm${f.id === fixtureId ? " btn-primary" : ""}`}
                  onClick={() => { setFixtureId(f.id); setSelection(null); setViewMoment(null); setPicks([]); }}>
            {f.label}
          </button>
        ))}
      </div>

      <div className="cockpit">
        <SessionRail cockpit={cockpit} selection={selection}
                     onSelect={(sel) => {
                       setSelection(sel);
                       // Same meaning as the real panel: only the current page's picks rung is a
                       // work-surface detour; everything else just points the inspector.
                       setViewMoment(sel.kind === "rung" && sel.id === `select:${panel.page ?? 1}`
                         ? "choose" : null);
                     }} />
        <WorkSurface
          panel={panel} cockpit={cockpit} viewMoment={viewMoment}
          onExitDetour={() => { setViewMoment(null); setSelection(null); }}
          busy={false} error="" call={call} decide={(b) => call("/apply_decide", b)}
          onFlag={(flag, detail) => call("/apply_flag", { flag, detail })}
          picks={picks} armed={null}
          onPick={(id) => setPicks((ps) => (ps.includes(id) ? ps.filter((x) => x !== id) : [...ps, id]))}
          onClear={() => setPicks([])}
          note={note} setNote={setNote} form={form} setForm={setForm}
        />
        <DecisionInspector panel={panel} cockpit={cockpit} selection={selection} />
      </div>

      {log.length > 0 && (
        <pre style={{ marginTop: 16, fontSize: 11, color: "var(--text-muted)",
                      background: "var(--surface-1)", border: "1px solid var(--line)",
                      borderRadius: 10, padding: 12, overflowX: "auto" }}>
          {log.join("\n")}
        </pre>
      )}
    </div>
  );
}
