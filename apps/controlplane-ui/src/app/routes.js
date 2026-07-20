export function parseAppPath(pathname) {
  const parts = pathname.split("/").filter(Boolean);
  if (!parts.length || parts[0] === "overview") return { view: "command" };
  if (parts[0] === "activity") return { view: "activity" };
  if (parts[0] === "domains") {
    return {
      view: "domains",
      domainId: parts[1] || null,
      tabId: parts[2] || "overview",
    };
  }
  if (parts[0] === "learning") return { view: "learning", sectionId: parts[1] || "overview" };
  if (parts[0] === "system") return { view: "system", sectionId: parts[1] || "status" };
  return { view: "command" };
}

export function pathForView(view, options = {}) {
  if (view === "command") return "/overview";
  if (view === "activity") return "/activity";
  if (view === "domains") {
    const domain = options.domainId ? `/${options.domainId}` : "";
    const tab = options.domainId && options.tabId && options.tabId !== "overview" ? `/${options.tabId}` : "";
    return `/domains${domain}${tab}`;
  }
  if (view === "learning") return `/learning/${options.sectionId || "overview"}`;
  if (view === "system") return `/system/${options.sectionId || "status"}`;
  return "/overview";
}

