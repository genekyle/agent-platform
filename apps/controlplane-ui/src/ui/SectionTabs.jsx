export function SectionTabs({ items, activeId, onChange, ariaLabel = "Page sections" }) {
  return (
    <div className="section-tabs" role="tablist" aria-label={ariaLabel}>
      {items.map((item) => (
        <button
          key={item.id}
          type="button"
          role="tab"
          aria-selected={item.id === activeId}
          className={`section-tab ${item.id === activeId ? "is-active" : ""}`}
          onClick={() => onChange(item.id)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

