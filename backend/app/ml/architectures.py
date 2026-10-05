"""
PyTorch Neural Network Architectures for SIH26143
1. SceneClassifier: 2-channel (VV+VH) input -> 3 classes (Oil, No-Oil, Lookalike)
2. SpillUNet: 2-channel (VV+VH) input -> 1 channel binary segmentation (Oil / Background)
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

class DoubleConv(nn.Module):
    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(in_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(out_ch, out_ch, 3, padding=1),
            nn.BatchNorm2d(out_ch),
            nn.ReLU(inplace=True)
        )
    def forward(self, x):
        return self.conv(x)

class SceneClassifier(nn.Module):
    """
    CNN architecture for 2-channel SAR input (VV, VH)
    Output logits: [Oil, No-Oil, Lookalike]
    """
    def __init__(self, in_channels: int = 2, num_classes: int = 3):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(in_channels, 16, kernel_size=3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            
            nn.Conv2d(16, 32, kernel_size=3, padding=1),
            nn.BatchNorm2d(32),
            nn.ReLU(),
            nn.MaxPool2d(2, 2),
            
            nn.Conv2d(32, 64, kernel_size=3, padding=1),
            nn.BatchNorm2d(64),
            nn.ReLU(),
            nn.AdaptiveAvgPool2d((1, 1))
        )
        self.fc = nn.Linear(64, num_classes)

    def forward(self, x):
        feat = self.features(x)
        feat = torch.flatten(feat, 1)
        logits = self.fc(feat)
        return logits

class SceneClassifierV2(nn.Module):
    """
    Improved 3-class (Oil / No-Oil / Lookalike) scene classifier - see
    `README_ML_STATUS.md` "SceneClassifierV2: root-cause and redesign" for
    the full reasoning. Two concrete, architectural problems in the original
    `SceneClassifier` motivated this:

    1. ONLY TWO DOWNSAMPLES BEFORE GLOBAL POOLING. The original model pools
       straight to 1x1 after just two MaxPool2d(2) layers - at a 256x256
       input, `AdaptiveAvgPool2d((1,1))` is averaging over a 64x64 feature
       map. An oil slick is typically a small, spatially LOCALIZED region
       against a large, uniform open-water background - averaging it in
       with ~64x64=4096 mostly-background feature-map cells overwhelmingly
       dilutes exactly the signal the model needs to key on, and gives the
       network almost no spatial hierarchy in which to build up
       texture/shape context before that collapse happens. This model adds
       two more downsampling stages (four MaxPool2d(2) total: 256 -> 16
       before pooling, a 16x smaller feature map than before at the point
       where it gets spatially collapsed) specifically so a small slick has
       been through enough convolution+pooling to be represented as a
       distinct, concentrated activation rather than being diluted at the
       very first pooling step.
    2. AVERAGE POOLING ONLY. Average pooling is appropriate for signals that
       fill the frame; it is a poor fit for "is there a small distinctive
       region anywhere in this image", which is closer to what oil-vs-
       lookalike discrimination actually is. This model pools with BOTH
       `AdaptiveAvgPool2d` and `AdaptiveMaxPool2d` and concatenates them -
       the max branch gives the network a channel that responds to "the
       single most salient location in the image", independent of how much
       of the frame that location occupies, which an average alone cannot
       represent at all.

    Also adds Dropout before the final linear layer (the original has none)
    - directly aimed at the generalization gap between training-distribution
    validation accuracy and genuinely independent test accuracy that
    motivated this redesign, not just at raw capacity.

    This is a NEW, ADDITIONAL class - `SceneClassifier` above is UNCHANGED
    and still what `app/ml/detector.py` loads by default, so every already-
    shipped checkpoint (`classifier_checkpoint.pth`, `classifier_real_*.pth`)
    keeps loading exactly as before; nothing is broken by adding this.
    `app/ml/train.py --architecture scene_classifier_v2` is how a developer
    with the real dataset opts into training this one instead - see that
    module and README_ML_STATUS.md for how to then evaluate and promote it.
    """
    def __init__(self, in_channels: int = 2, num_classes: int = 3, dropout: float = 0.3):
        super().__init__()
        self.features = nn.Sequential(
            DoubleConv(in_channels, 16),
            nn.MaxPool2d(2, 2),   # 256 -> 128

            DoubleConv(16, 32),
            nn.MaxPool2d(2, 2),   # 128 -> 64

            DoubleConv(32, 64),
            nn.MaxPool2d(2, 2),   # 64 -> 32

            DoubleConv(64, 128),
            nn.MaxPool2d(2, 2),   # 32 -> 16 (vs 64 at this stage in SceneClassifier)
        )
        self.avg_pool = nn.AdaptiveAvgPool2d((1, 1))
        self.max_pool = nn.AdaptiveMaxPool2d((1, 1))
        self.dropout = nn.Dropout(dropout)
        self.fc = nn.Linear(128 * 2, num_classes)  # *2: avg-pool branch concatenated with max-pool branch

    def forward(self, x):
        feat = self.features(x)
        pooled = torch.cat([self.avg_pool(feat), self.max_pool(feat)], dim=1)
        pooled = torch.flatten(pooled, 1)
        pooled = self.dropout(pooled)
        logits = self.fc(pooled)
        return logits


class SpillUNet(nn.Module):
    """
    U-Net architecture for 2-channel SAR input (VV, VH)
    Output logit mask: (B, 1, H, W) for Oil vs Background segmentation
    """
    def __init__(self, in_channels: int = 2, out_channels: int = 1):
        super().__init__()
        self.inc = DoubleConv(in_channels, 16)
        self.down1 = nn.Sequential(nn.MaxPool2d(2), DoubleConv(16, 32))
        self.down2 = nn.Sequential(nn.MaxPool2d(2), DoubleConv(32, 64))
        
        self.up1 = nn.ConvTranspose2d(64, 32, 2, stride=2)
        self.conv_up1 = DoubleConv(64, 32)
        
        self.up2 = nn.ConvTranspose2d(32, 16, 2, stride=2)
        self.conv_up2 = DoubleConv(32, 16)
        
        self.outc = nn.Conv2d(16, out_channels, 1)

    def forward(self, x):
        x1 = self.inc(x)
        x2 = self.down1(x1)
        x3 = self.down2(x2)
        
        x_up1 = self.up1(x3)
        x_up1 = torch.cat([x_up1, x2], dim=1)
        x_up1 = self.conv_up1(x_up1)
        
        x_up2 = self.up2(x_up1)
        x_up2 = torch.cat([x_up2, x1], dim=1)
        x_up2 = self.conv_up2(x_up2)
        
        logits = self.outc(x_up2)
        return logits

if __name__ == "__main__":
    classifier = SceneClassifier()
    segmenter = SpillUNet()
    dummy_input = torch.randn(2, 2, 256, 256)
    c_out = classifier(dummy_input)
    s_out = segmenter(dummy_input)
    print(f"Classifier output shape: {c_out.shape}")
    print(f"Segmenter output shape: {s_out.shape}")
