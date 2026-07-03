import { MODE_COPY } from "./domains";

// The automation-mode control — the clean bridge from manual to automated. Controlled by the
// workspace (which owns the persisted setting), so Status / Goals / Tasks all see the same mode.
const ORDER = ["manual", "supervised", "autopilot"];

export function AutomationMode({ mode, onChange, saving }) {
  const active = ORDER.includes(mode) ? mode : "manual";
  return (
    <div>
      <div className="mode-toggle" role="group" aria-label="Automation mode">
        {ORDER.map((m) => (
          <button
            key={m}
            className={m === active ? "is-active" : ""}
            disabled={saving}
            onClick={() => m !== active && onChange(m)}
          >
            {MODE_COPY[m].label}
          </button>
        ))}
      </div>
      <div className="mode-hint">{MODE_COPY[active].hint}</div>
    </div>
  );
}
