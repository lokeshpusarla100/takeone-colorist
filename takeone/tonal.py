"""Shared, footage-independent tonal definitions.

Every constant is a property of display-encoded Rec.709 or of perception, never fitted
to a particular video. The highlight gate (render_preflight), review evidence
(evidence) and the revision convergence rule (convergence) all use these, so "visible"
and "highlight" mean the same thing everywhere.
"""
import numpy as np

WEIGHTS = np.array([.2126, .7152, .0722], np.float32)
# Smallest tone shift treated as visible: 0.02 encoded (~5 8-bit codes, about one
# just-noticeable step on a flat area).
VISIBLE_CHANGE = .02
# Photographic 18% grey, Rec.709 OETF encoded (1.099*0.18**0.45-0.099).
MID_GREY = .409
# Highlights: a frame's brightest 10%, counting only pixels at or above encoded 0.6.
HIGHLIGHT_QUANTILE = .9
HIGHLIGHT_FLOOR = .6
# Fewer highlight pixels than this means the frame has no assessable highlights.
MIN_HIGHLIGHT_FRACTION = .005


def highlight_mask(luma):
    """Boolean mask of a frame's real highlights, or None when it has none worth assessing."""
    high = luma >= max(HIGHLIGHT_FLOOR, float(np.quantile(luma, HIGHLIGHT_QUANTILE)))
    if high.mean() < MIN_HIGHLIGHT_FRACTION or high.all():
        return None
    return high
