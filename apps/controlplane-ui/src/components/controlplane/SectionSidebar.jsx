export function SectionSidebar({ title, sections, activeSectionId, onSelectSection }) {
  return (
    <aside className="section-sidebar">
      <div className="section-sidebar-head">
        <div className="section-sidebar-kicker">Section</div>
        <div className="section-sidebar-title">{title}</div>
      </div>

      <nav className="section-nav">
        {sections.map((section) => (
          <button
            key={section.id}
            className={`section-nav-item ${activeSectionId === section.id ? "active" : ""}`}
            onClick={() => onSelectSection(section.id)}
          >
            <span className="section-nav-label">{section.label}</span>
            <span className="section-nav-subtitle">{section.subtitle}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
