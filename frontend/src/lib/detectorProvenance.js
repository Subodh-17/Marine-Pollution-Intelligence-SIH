// Single source of truth for how each possible `satellite.detector_provenance`
// value is presented in the UI.
//
// Backend (see backend/app/investigation/orchestrator.py) only ever
// produces exactly one of these values - never a fifth, and never
// silently defaults to an ambiguous label. The frontend mirrors that: any
// unrecognized value here is treated as a bug to surface, not something to
// paper over with a generic fallback string.

export const DETECTOR_PROVENANCE = {
  CLASSICAL: 'CLASSICAL_DARK_SPOT',
  ML: 'ML_PROTOTYPE_SYNTHETIC_TRAINED',
  ML_REAL: 'ML_REAL_ZENODO_TRAINED',
  ML_FALLBACK: 'CLASSICAL_DARK_SPOT_ML_FALLBACK',
}

const PROVENANCE_INFO = {
  [DETECTOR_PROVENANCE.CLASSICAL]: {
    shortLabel: 'SAR Detection',
    badge: 'SAR Detection',
    confidenceSub: 'Heuristic detection confidence',
    confidenceInline: '(heuristic)',
    confidenceCaveat:
      'Heuristic detection confidence — a 0–100% plausibility index (elongation, texture, ' +
      'solidity, size), not a calibrated statistical probability.',
  },
  [DETECTOR_PROVENANCE.ML]: {
    shortLabel: 'AI Model (Development Dataset)',
    badge: 'AI Model — Development Dataset',
    confidenceSub: 'Uncalibrated prototype softmax confidence',
    confidenceInline: '(experimental model confidence)',
    confidenceCaveat:
      'Experimental model confidence — the ML prototype classifier\u2019s own raw softmax output. ' +
      'It is not a calibrated statistical probability and the model is not validated on real ' +
      'Sentinel-1 data (see Model Card).',
  },
  [DETECTOR_PROVENANCE.ML_REAL]: {
    shortLabel: 'AI Model (Zenodo Trained)',
    badge: 'AI Model — Zenodo Trained',
    confidenceSub: 'Real-data-trained model confidence',
    confidenceInline: '(real-data-trained model confidence)',
    confidenceCaveat:
      'The model\u2019s own softmax output, from a checkpoint trained on the real Zenodo ' +
      'Sentinel-1 SAR Oil Spill Dataset. It is still not a calibrated statistical probability - ' +
      'see the model\u2019s evaluation report for held-out test-set metrics before relying on it ' +
      'in a new deployment context (see Model Card \u00a76b).',
  },
  [DETECTOR_PROVENANCE.ML_FALLBACK]: {
    shortLabel: 'SAR Analysis (Alternate Processing)',
    badge: 'SAR Analysis — Alternate Processing',
    confidenceSub: 'Heuristic confidence (ML fallback)',
    confidenceInline: '(heuristic — ML fallback)',
    confidenceCaveat:
      'Heuristic detection confidence — ML detection was requested but did not produce a usable ' +
      'result, so the classical detector\u2019s plausibility index is reported instead. This is NOT ' +
      'an ML confidence score; see detection notes for the specific fallback reason.',
  },
}

const UNKNOWN_PROVENANCE_INFO = {
  shortLabel: 'Unknown detector',
  badge: 'Unrecognized detector provenance',
  confidenceSub: 'Detection confidence (provenance unrecognized)',
  confidenceInline: '(unrecognized provenance)',
  confidenceCaveat:
    'This result\u2019s detector_provenance value was not one of the known states ' +
    '(CLASSICAL_DARK_SPOT / ML_PROTOTYPE_SYNTHETIC_TRAINED / ML_REAL_ZENODO_TRAINED / ' +
    'CLASSICAL_DARK_SPOT_ML_FALLBACK). Treat this confidence number with extra caution and ' +
    'report it as a bug.',
}

export function getProvenanceInfo(provenance) {
  return PROVENANCE_INFO[provenance] || UNKNOWN_PROVENANCE_INFO
}

export function isKnownProvenance(provenance) {
  return Object.prototype.hasOwnProperty.call(PROVENANCE_INFO, provenance)
}
