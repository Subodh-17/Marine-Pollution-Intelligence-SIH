export function formatSourceLabel(source) {
  if (!source) return source
  if (source === 'USER_UPLOADED_REAL_IMAGE') return 'Satellite Observation'
  if (source === 'SYNTHETIC_DEMO') return 'Illustrative Scenario'
  return source
}

export function formatMethodLabel(method) {
  if (!method) return method
  if (method === 'CLASSICAL_DARK_SPOT') return 'SAR Detection'
  if (method === 'hybrid_dark_spot_morphology') return 'SAR Feature Analysis'
  return method
}
