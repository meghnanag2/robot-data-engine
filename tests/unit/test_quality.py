import numpy as np

from rde.quality.frame_metrics import average_hash, blur_score, frame_metrics, to_gray
from rde.quality.rules import CleaningRules, Reason, frame_reason


def _checker(n=64):
    tile = (np.indices((n, n)).sum(axis=0) // 8) % 2 * 255
    return np.stack([tile] * 3, axis=-1).astype(np.uint8)


def test_sharp_image_scores_higher_than_flat():
    sharp = to_gray(_checker())
    flat = np.full((64, 64), 128.0)
    assert blur_score(sharp) > 1000
    assert blur_score(flat) == 0


def test_identical_frames_share_hash_and_different_frames_do_not():
    a = to_gray(_checker())
    assert average_hash(a) == average_hash(a.copy())
    assert average_hash(a) != average_hash(255 - a)
    assert len(average_hash(a)) == 16


def test_frame_rules():
    rules = CleaningRules()
    dark = frame_metrics(np.zeros((32, 32, 3), dtype=np.uint8))
    assert frame_reason(dark, rules) == Reason.TOO_DARK
    assert frame_reason(frame_metrics(_checker()), rules) is None


def test_rules_from_json_ignores_unknown_keys():
    r = CleaningRules.from_json('{"z_max": 4, "not_a_rule": 1}')
    assert r.z_max == 4
    assert CleaningRules.from_json(r.to_json()) == r


def test_bright_frame_with_detail_is_not_overexposed():
    """PushT renders objects on a white background: bright but full of detail."""
    rules = CleaningRules()
    frame = np.full((64, 64, 3), 255, dtype=np.uint8)
    frame[20:40, 20:40] = 0  # a dark object on white
    m = frame_metrics(frame)
    assert m["brightness"] > rules.max_brightness - 50 and m["contrast"] > rules.min_contrast
    assert frame_reason(m, rules) is None
    flat_white = frame_metrics(np.full((32, 32, 3), 255, dtype=np.uint8))
    assert frame_reason(flat_white, rules) == Reason.OVEREXPOSED
