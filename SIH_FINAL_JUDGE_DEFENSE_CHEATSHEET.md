# Smart India Hackathon (SIH) — Final Judge Defense Cheatsheet
## Marine Pollution Intelligence & Spill-Aware Navigation Platform (SIH-26143)

**Document Purpose:** Rapid-fire 30-second response guide for the 3-minute jury cross-examination. Use these structured, defensible answers to address technical scrutiny from AI researchers, defense evaluators, and maritime domain specialists.

---

### Q1: "How do you prove that your models didn't overfit or leak test data during training?"
- **Answer:** *"Our evaluation is based on **Zenodo Part III** (`10.5281/zenodo.13761290`), an independent external benchmark published separately from Part I/II. We executed an automated cryptographic audit: there are **zero scene ID overlaps and zero SHA-256 byte hash collisions** between train and test sets. Furthermore, our preprocessing (`sar_percentile_1_99_minmax_v1`) computes percentiles **strictly per-sample**, meaning no global dataset statistics are ever shared."*

---

### Q2: "Can your models generalize to other SAR satellites like RISAT-1 or TerraSAR-X?"
- **Answer:** *"Our model is specifically calibrated for **Sentinel-1 C-band (5.405 GHz) IW GRD dual-pol** data. SAR physics depends on Bragg scattering resonance. In wind regimes between $3\text{ m/s}$ and $12\text{ m/s}$, C-band radar reliably detects wave damping. We do not claim universal sensor invariance; transfer to X-band (TerraSAR-X) or S-band (ISRO RISAT) requires sensor-specific radiometric recalibration of $\sigma_0$ backscatter."*

---

### Q3: "What prevents the Coast Guard from scrambling expensive aircraft on false alarms?"
- **Answer:** *"On our 150 held-out lookalike test scenes, our calibrated classifier achieved a **96.00% Lookalike Rejection Rate**—only 5 false alarms (3.33% false alarm rate). More importantly, when we experimented with multiscale segmentation, it increased lookalike false positive pixels to 8.74%, violating our $\le 4.5\%$ safety gate. Following fail-safe military surveillance principles, we **disabled multiscale segmentation by default** to protect operational resources."*

---

### Q4: "Why do you report two different accuracy numbers: 79.33% and 78.44%?"
- **Answer:** *"That difference is an engineering reality of deployment frameworks. Both were evaluated on the exact same 450 held-out test scenes. The 4-scene difference is caused by sub-pixel bilinear coordinate interpolation differences between `scipy.ndimage.zoom` (CPU production preprocessing) and `torch.nn.functional.interpolate`. When fed identical tensors, PyTorch and ONNX logits match to $10^{-5}$ precision. $79.33\%$ is our genuine production ONNX CPU performance."*

---

### Q5: "What happens when external ocean weather APIs (NOAA / Open-Meteo) time out or go down?"
- **Answer:** *"Our hydrodynamic engine features **graceful deterministic degradation**. If real-time API feeds time out, the backend automatically falls back to Indian Ocean seasonal climatological current and wind vectors without interrupting the user. Concurrently, forward forecasts display expanding Gaussian dispersion ellipses ($K_h \approx 10-50\text{ m}^2/\text{s}$) to transparently communicate spatial uncertainty."*

---

### Q6: "Offending ships turn off their AIS transponders. How do you handle 'dark ships'?"
- **Answer:** *"Our attribution engine scans historical trajectories for kinematic anomalies—such as transponder silences or sudden position jumps exceeding 30 knots. If a vessel goes dark near the hindcasted release point, its priority score is elevated. However, we do not claim supernatural tracking: when AIS is disabled, our platform gives Coast Guard commanders the **hindcasted origin coordinates and temporal release window**, enabling correlation with Coastal Surveillance Radar or naval patrol flights."*

---

### Q7: "Can your system's findings be used in a court of law to convict a shipowner under MARPOL?"
- **Answer:** *"No, and we explicitly instruct operators on this boundary. The platform produces **investigative prioritization leads**, not judicial proof of criminal guilt. Remote sensing and AIS correlation establish circumstantial spatial-temporal alignment. Judicial prosecution under MARPOL Annex I requires physical hydrocarbon sampling (gas chromatography/mass spectrometry) to chemically match the sea slick with oil in the ship's bilge. We provide the target coordinates for aerial interception."*

---

### Q8: "Could your visibility-tangent route send a vessel through shallow reefs or rocks?"
- **Answer:** *"The platform uses pre-configured maritime corridor graphs connecting 11 major Indian ports with established deep-water Traffic Separation Schemes (TSS). Avoidance tangents are calculated around the buffered convex hull of the slick (+1.5 NM). Furthermore, every route payload embeds mandatory disclaimers under **SOLAS Chapter V, Regulation 34 and IMO Resolution A.893(21)**, reminding mariners that master's professional discretion and official ENC charts remain sovereign."*

---

### Q9: "How is fuel consumption calculated, and why does reducing speed save money on a detour?"
- **Answer:** *"Fuel is computed using the **Admiralty power formula**: $P = (\Delta^{2/3} \cdot V^3) / C_{\text{adm}}$. Because propulsion power scales with the **cube of speed ($V^3$)**, reducing voyage speed from $14.5\text{ kts}$ down to $12.0\text{ kts}$ reduces hourly bunker consumption from $1.88\text{ MT/h}$ to $1.08\text{ MT/h}$—saving over $40\%$ fuel per hour. This enables shipmasters to absorb the fuel cost of a 25 NM detour entirely through optimized slow steaming."*

---

### Q10: "How does your architecture scale to handle hundreds of satellite passes daily?"
- **Answer:** *"Both SpillUNet ($54\text{ KB}$) and the Classifier ($50\text{ KB}$) run on **ONNX Runtime CPU in under 45 milliseconds**. We require zero expensive GPU clusters. The FastAPI backend is stateless and horizontally scalable via Docker containers on Render, while the React 19 frontend is distributed globally across Vercel's edge network. A single commodity 4-core server can process over 1,500 satellite scenes per hour."*
