"""Cleaning thresholds and reason codes, shared by every stage.

Thresholds come from Terraform (one place), so changing a rule is a reviewed code change.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class CleaningRules:
    fps: float = 10.0
    z_max: float = 6.0  # state value further than z_max std devs -> out of range
    action_spike: float = 50.0  # flag (keep) frame-to-frame action jumps above this
    min_episode_frames: int = 10
    max_gap_frames: float = 1.5  # timestamp gap above this many frames -> episode rejected
    min_brightness: float = 15.0
    max_brightness: float = 245.0
    min_blur_score: float = 10.0
    # Exposure rules need low contrast too: simulators (e.g. PushT) render on white backgrounds,
    # so a bright frame with visible objects is fine. The first real run flagged 100% of PushT
    # frames as overexposed under a brightness-only rule.
    min_contrast: float = 10.0

    @classmethod
    def from_json(cls, raw: str | None) -> CleaningRules:
        if not raw:
            return cls()
        data = json.loads(raw)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_json(self) -> str:
        return json.dumps(asdict(self))


class Reason:
    NULL_VALUES = "null_values"
    NAN_VALUES = "nan_values"
    DUPLICATE_FRAME = "duplicate_frame"
    OUT_OF_RANGE_STATE = "out_of_range_state"
    TOO_SHORT = "too_short"
    FRAME_GAP = "frame_gap"
    TIMESTAMP_GAP = "timestamp_gap"
    TOO_DARK = "too_dark"
    OVEREXPOSED = "overexposed"
    BLURRY = "blurry"
    NEAR_DUPLICATE = "near_duplicate"
    NO_EPISODE_MATCH = "no_episode_match"


def frame_reason(m: dict, rules: CleaningRules) -> str | None:
    """Image rules for one extracted frame (duplicate check needs the whole set, so it lives in Spark)."""
    low_contrast = m.get("contrast", 0.0) < rules.min_contrast
    if m["brightness"] < rules.min_brightness and low_contrast:
        return Reason.TOO_DARK
    if m["brightness"] > rules.max_brightness and low_contrast:
        return Reason.OVEREXPOSED
    if m["blur_score"] < rules.min_blur_score:
        return Reason.BLURRY
    return None
