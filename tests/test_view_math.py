import math

import pytest

from stickrig.view_math import pan_sensitivity


def test_zoom_sensitive_pan_is_smooth_and_bounded():
    values = [pan_sensitivity(z) for z in (0.05, 0.25, 0.5, 1.0, 2.0, 5.0, 12)]
    assert values == sorted(values, reverse=True)
    assert values[3] == 1.0
    assert min(values) >= 0.65
    assert max(values) <= 1.8
    assert abs(pan_sensitivity(0.99) - pan_sensitivity(1.01)) < 0.01


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_zoom_rejected(value):
    with pytest.raises(ValueError):
        pan_sensitivity(value)
