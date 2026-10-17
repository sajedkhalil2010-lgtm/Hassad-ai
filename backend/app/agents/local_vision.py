"""local_vision — 100% local quantitative plant-pathology vision engine.

OpenCV + NumPy only: **no external APIs, no network, no rate limits, no
429s**. Every number this module reports is computed from the actual
pixels of the uploaded photo — deterministic, reproducible, auditable.

Pipeline (all bounded, sub-second on the default 960 px working size):

1. **Leaf segmentation** — vegetation mask in HSV (hue/saturation/value
   bands), morphological cleanup, largest connected component. When the
   vegetation mask covers most of the frame (green background or a full
   canopy shot) the engine switches to *canopy mode* and treats the
   visible canopy as the reference area — the agronomically standard
   way to express damage on real field photos.
2. **Lesion segmentation — heuristic class union** inside the leaf
   region, by colour family:
   * ``necrotic`` — brown / dark red-brown dead tissue,
   * ``water_soaked`` — very dark water-soaked patches (blight signature),
   * ``chlorotic`` — yellow chlorotic tissue (deficiency / halos),
   * ``powdery`` — bright desaturated surface (powdery mildew signature),
   * ``rust_ochre`` — orange-ochre pustules (rust signature).
3. **Consensus confidence** — an *independent* second segmentation runs
   in CIE-Lab space (distance from the healthy-green reference,
   Otsu-thresholded). The reported
   ``segmentation_dice_score`` is the Dice coefficient between the two
   independent masks (``2|A∩B| / (|A|+|B|)``): method agreement, i.e. an
   honest, computable confidence — not a fabricated accuracy. Both
   masks empty → 1.0 (perfect agreement on "no lesion"); one empty → 0.0.
4. **Quantitative metrics** — ``affected_area_percentage`` (lesion px /
   leaf px × 100), lesion count, largest-lesion share (coalescence),
   per-family colour fractions, halo index (chlorotic ring around
   necrotic cores — the early-blight signature), and severity
   categorisation from configurable thresholds: Early (< 5 %),
   Moderate (≤ 15 %), Critical (> 15 %).

Tailored for the semi-arid Tébessa region (Algeria): colour bands were
chosen to separate sun-lit field greens from the ochre/brown soils and
stress patterns typical of steppe agriculture.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image

from app.core.config import get_settings

logger = logging.getLogger("hassad.agents.local_vision")

# HSV working bands (OpenCV hue: 0-179).
_HUE_GREEN = (20, 95)
_HUE_YELLOW = (22, 35)
_HUE_NECROTIC = (0, 20)  # + red-brown tail >= 160
_HUE_RUST = (8, 25)
_MIN_LESION_PX = 24  # below this a component is noise, not a lesion
_MIN_LEAF_FRAC = 0.01  # vegetation must cover >=1% of frame to be a leaf


@dataclass
class VisionMetrics:
    """Quantitative pathology metrics computed from real pixels."""

    affected_area_percentage: float
    segmentation_dice_score: float
    severity: str  # "none" | "early" | "moderate" | "critical"
    leaf_area_px: int
    lesion_area_px: int
    lesion_count: int
    largest_lesion_share: float
    color_fractions: dict[str, float] = field(default_factory=dict)
    halo_index: float = 0.0
    border_necrosis_share: float = 0.0
    mode: str = "leaf"  # "leaf" (single leaf) | "canopy" (full-frame vegetation)
    latency_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "affected_area_percentage": self.affected_area_percentage,
            "segmentation_dice_score": self.segmentation_dice_score,
            "severity": self.severity,
            "lesion_count": self.lesion_count,
            "largest_lesion_share": self.largest_lesion_share,
            "color_fractions": self.color_fractions,
            "halo_index": self.halo_index,
            "border_necrosis_share": self.border_necrosis_share,
            "mode": self.mode,
            "leaf_area_px": self.leaf_area_px,
            "lesion_area_px": self.lesion_area_px,
        }


class VisionProcessingError(RuntimeError):
    """The image could not be analysed locally (no segmentation possible)."""


# ======================================================================
# Core segmentation
# ======================================================================
def _to_bgr_array(image: Image.Image, max_side: int) -> np.ndarray:
    """Downscale + convert PIL → BGR uint8 (the colour OpenCV expects)."""
    img = image.convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side))
    return np.asarray(img)[:, :, ::-1].copy()  # RGB → BGR


def _largest_component(mask: np.ndarray) -> np.ndarray:
    """Keep only the largest connected component (the leaf itself)."""
    count, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    if count <= 2:  # background + (maybe) one component
        return mask
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0  # ignore background
    return labels == int(sizes.argmax())


def _clean(mask: np.ndarray) -> np.ndarray:
    """Morphological cleanup: close holes, open specks."""
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return mask.astype(bool)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Fill internal holes of the vegetation mask.

    Powdery-mildew patches, necrotic cores and bright veins are *not*
    vegetation-coloured, so a pure hue mask would punch holes exactly
    where the symptoms are. Filling the outer contour keeps the whole
    leaf blade (including symptomatic tissue) as the reference area.
    """
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return mask
    filled = np.zeros_like(mask, dtype=np.uint8)
    cv2.drawContours(filled, contours, -1, 1, thickness=cv2.FILLED)
    return filled.astype(bool)


def _leaf_mask(hsv: np.ndarray) -> tuple[np.ndarray, str]:
    """Vegetation segmentation → (leaf_mask, mode).

    * vegetation = green/yellow-green hue + saturation + brightness,
    * cleaned + hole-filled + largest component,
    * if vegetation covers > 85% of the frame → *canopy mode*: the whole
      visible frame is the reference area (green backgrounds / dense
      canopies), otherwise the largest blob is the leaf.
    """
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    veg = (
        (h >= _HUE_GREEN[0]) & (h <= _HUE_GREEN[1])
        & (s >= 40) & (v >= 40)
    )
    veg = _fill_holes(_clean(veg))
    coverage = float(veg.mean())
    if coverage >= 0.85:
        return veg, "canopy"
    if coverage < _MIN_LEAF_FRAC:
        # No vegetation blob (unusual background): fall back to Otsu on
        # brightness so the caller still gets an honest segmentation.
        _t, otsu = cv2.threshold(
            hsv[..., 2], 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )
        mask = _clean(otsu.astype(bool))
        return _largest_component(mask), "leaf"
    return _largest_component(veg), "leaf"


def _lesion_classes(hsv: np.ndarray) -> dict[str, np.ndarray]:
    """Heuristic colour-family masks (independent of the leaf mask)."""
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    necrotic = (((h <= _HUE_NECROTIC[1]) | (h >= 160)) & (s >= 50) & (v <= 140))
    water_soaked = (v <= 70) & (s >= 30)
    chlorotic = ((h >= _HUE_YELLOW[0]) & (h <= _HUE_YELLOW[1]) & (s >= 45) & (v >= 90))
    powdery = (s <= 40) & (v >= 170)
    rust_ochre = (
        (h >= _HUE_RUST[0]) & (h <= _HUE_RUST[1]) & (s >= 55) & (v >= 140)
    )
    return {
        "necrotic": necrotic,
        "water_soaked": water_soaked,
        "chlorotic": chlorotic,
        "powdery": powdery,
        "rust_ochre": rust_ochre,
    }


def _consensus_lesion_mask(bgr: np.ndarray, leaf_mask: np.ndarray) -> np.ndarray:
    """Independent Lab-space segmentation (the second opinion for Dice)."""
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2Lab).astype(np.float32)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = hsv[..., 0]
    healthy = leaf_mask & (h >= 35) & (h <= 95)
    if int(healthy.sum()) < 50:  # no healthy reference → use all leaf px
        healthy = leaf_mask
    ref = np.median(lab[healthy], axis=0)
    dist = np.linalg.norm(lab - ref, axis=2)
    dist[~leaf_mask] = 0.0
    dist_u8 = np.clip(dist, 0, 255).astype(np.uint8)
    _t, binary = cv2.threshold(dist_u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    mask = _clean(binary.astype(bool) & leaf_mask)
    return mask & _leaf_filter_components(mask)


def _leaf_filter_components(mask: np.ndarray) -> np.ndarray:
    """Drop tiny specks (noise) from a lesion mask."""
    if not mask.any():
        return mask
    count, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    keep = sizes >= _MIN_LESION_PX
    return keep[labels]


def _dice(a: np.ndarray, b: np.ndarray) -> float:
    a_n, b_n = int(a.sum()), int(b.sum())
    if a_n == 0 and b_n == 0:
        return 1.0
    if a_n == 0 or b_n == 0:
        return 0.0
    return float(2.0 * np.logical_and(a, b).sum() / (a_n + b_n))


# ======================================================================
# The public entry point
# ======================================================================
def analyze_leaf(image: Image.Image) -> VisionMetrics:
    """Run the full local pipeline and return quantitative metrics."""
    settings = get_settings()
    started = time.perf_counter()

    bgr = _to_bgr_array(image, settings.CV_MAX_SIDE)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    leaf, mode = _leaf_mask(hsv)
    leaf_area = int(leaf.sum())
    if leaf_area < 64:
        raise VisionProcessingError("no leaf region could be segmented")

    # --- heuristic lesion classes, union + cleanup ---------------------
    classes = _lesion_classes(hsv)
    class_masks: dict[str, np.ndarray] = {}
    for name, raw in classes.items():
        cleaned = _clean(raw & leaf)
        class_masks[name] = _leaf_filter_components(cleaned)
    lesion = np.logical_or.reduce(list(class_masks.values()))
    lesion_area = int(lesion.sum())

    # --- independent second opinion → consensus Dice -------------------
    consensus = _consensus_lesion_mask(bgr, leaf)
    dice = _dice(lesion, consensus)

    # --- quantitative metrics ------------------------------------------
    affected = round(100.0 * lesion_area / leaf_area, 2)
    lesion_count = 0
    largest_share = 0.0
    if lesion_area:
        count, labels = cv2.connectedComponents(lesion.astype(np.uint8), connectivity=8)
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        sizes = sizes[sizes >= _MIN_LESION_PX]
        lesion_count = int(sizes.size)
        if sizes.size:
            largest_share = round(float(sizes.max()) / lesion_area, 3)

    fractions = {
        name: round(float((mask & leaf).sum()) / leaf_area, 4)
        for name, mask in class_masks.items()
    }

    # halo index: chlorotic tissue surrounding necrotic cores (early blight
    # signature) — dilate necrosis, intersect chlorosis.
    halo = 0.0
    nec = class_masks["necrotic"]
    chl = class_masks["chlorotic"]
    if nec.any() and chl.any():
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        dilated = cv2.dilate(nec.astype(np.uint8), kernel).astype(bool) & leaf
        halo = round(float((dilated & chl).sum()) / max(int(chl.sum()), 1), 3)

    # border necrosis share (potassium / drought marginal burn proxy)
    border_share = 0.0
    if nec.any():
        eroded = cv2.erode(
            leaf.astype(np.uint8),
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (41, 41)),
        ).astype(bool)
        border = leaf & ~eroded
        border_share = round(float((nec & border).sum()) / max(int(nec.sum()), 1), 3)

    severity = _severity(affected, settings.SEVERITY_EARLY_MAX_PCT,
                         settings.SEVERITY_MODERATE_MAX_PCT)

    return VisionMetrics(
        affected_area_percentage=affected,
        segmentation_dice_score=round(dice, 3),
        severity=severity,
        leaf_area_px=leaf_area,
        lesion_area_px=lesion_area,
        lesion_count=lesion_count,
        largest_lesion_share=largest_share,
        color_fractions=fractions,
        halo_index=halo,
        border_necrosis_share=border_share,
        mode=mode,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


def _severity(affected: float, early_max: float, moderate_max: float) -> str:
    if affected <= 0.0:
        return "none"
    if affected < early_max:
        return "early"
    if affected <= moderate_max:
        return "moderate"
    return "critical"


async def analyze_leaf_async(image: Image.Image) -> VisionMetrics:
    """CPU-heavy segmentation off the event loop."""
    return await asyncio.to_thread(analyze_leaf, image)
