import torch
from pathlib import Path
from app.ml.detector import get_ml_engine

print("--- PYTORCH ENV ---")
print("Version:", torch.__version__)
print("CUDA Version:", torch.version.cuda)
print("CUDA Available:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
    print("Testing Tensor:")
    x = torch.rand(5, 5).cuda()
    y = torch.rand(5, 5).cuda()
    print("Tensor OP Success:", (x @ y).shape == (5, 5))

print("\n--- ML ENGINE TESTS ---")
# Explicitly initialize the real classifier and segmentation checkpoint paths
# They reside in backend/data/ml_checkpoints/
checkpoint_dir = Path("data/ml_checkpoints")
classifier_path = checkpoint_dir / "classifier_checkpoint.pth"
segmenter_path = checkpoint_dir / "spill_unet.pt"
print(f"Classifier exists: {classifier_path.exists()}")
print(f"Segmenter exists: {segmenter_path.exists()}")

# Load engine
engine = get_ml_engine()

if engine:
    print("\n--- LOAD SUCCESS ---")
    print("Classifier loaded:", engine.classifier is not None)
        
    print("\n--- INFERENCE TEST ---")
    # Quick mock input
    import numpy as np
    mock_input = np.random.rand(512, 512).astype(np.float32)
    score, mask = engine.detect_and_segment(mock_input)
    print("Score type:", type(score))
    print("Mask shape:", mask.shape if mask is not None else mask)
else:
    print("Engine failed to load. TORCH_AVAILABLE was likely false.")
