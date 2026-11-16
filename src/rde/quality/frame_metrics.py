"""Per-frame image quality metrics (numpy only, so they unit test without AWS)."""

from __future__ import annotations

import numpy as np


def to_gray(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.float32)
    return 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]


def brightness(gray: np.ndarray) -> float:
    """Mean intensity: 0 black, 255 white."""
    return float(gray.mean())


def contrast(gray: np.ndarray) -> float:
    """Pixel standard deviation; low means a flat frame (no visible detail)."""
    return float(gray.std())


def blur_score(gray: np.ndarray) -> float:
    """Variance of the Laplacian; low means blurry or flat."""
    if gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    lap = gray[:-2, 1:-1] + gray[2:, 1:-1] + gray[1:-1, :-2] + gray[1:-1, 2:] - 4.0 * gray[1:-1, 1:-1]
    return float(lap.var())


def average_hash(gray: np.ndarray, size: int = 8) -> str:
    """64-bit average hash (hex). Equal hashes mean near-identical frames."""
    h, w = gray.shape
    ys = np.linspace(0, h, size + 1, dtype=int)
    xs = np.linspace(0, w, size + 1, dtype=int)
    small = np.array(
        [[gray[ys[i] : ys[i + 1], xs[j] : xs[j + 1]].mean() for j in range(size)] for i in range(size)]
    )
    value = 0
    for bit in (small > small.mean()).flatten():
        value = (value << 1) | int(bit)
    return f"{value:0{size * size // 4}x}"


def frame_metrics(rgb: np.ndarray) -> dict:
    gray = to_gray(rgb)
    return {
        "height": int(rgb.shape[0]),
        "width": int(rgb.shape[1]),
        "brightness": round(brightness(gray), 3),
        "contrast": round(contrast(gray), 3),
        "blur_score": round(blur_score(gray), 3),
        "ahash": average_hash(gray),
    }
