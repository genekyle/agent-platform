export function fmt(ts) {
  if (!ts) return "-";
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return String(ts);
  }
}

export function getStatusClass(status) {
  const normalized = String(status || "").toLowerCase();

  if (normalized.includes("success")) return "status-pill success";
  if (normalized.includes("running")) return "status-pill running";
  if (normalized.includes("blocked")) return "status-pill blocked";
  if (normalized.includes("fail")) return "status-pill failed";
  if (normalized.includes("busy")) return "status-pill running";
  if (normalized.includes("idle")) return "status-pill neutral";

  return "status-pill neutral";
}

export function resolveBbox(candidate, acquisition) {
  if (candidate?.grounding?.bbox) return candidate.grounding.bbox;
  const el = (acquisition?.actionable_elements ?? []).find(
    (entry) => entry.uid === candidate?.element_id,
  );
  return el?.rect ?? null;
}

export function screenshotFilename(artifact) {
  const refs = artifact?.acquisition?.screenshots ?? [];
  if (!refs.length) return null;
  if (refs[0].filename) return refs[0].filename;
  const path = refs[0].path ?? "";
  return path.split("/").pop() || null;
}

export function candidateLabelsFromAnnotation(annotation) {
  if (!annotation) return {};
  if (annotation.candidate_labels) return { ...annotation.candidate_labels };

  const labels = {};
  if (annotation.positive_candidate_id) {
    labels[annotation.positive_candidate_id] = "approve";
  }
  for (const candidateId of annotation.rejected_candidate_ids ?? []) {
    labels[candidateId] = "reject";
  }
  return labels;
}

export function positiveCandidateIdFromLabels(labels) {
  return Object.entries(labels).find(([, value]) => value === "approve")?.[0] ?? null;
}
