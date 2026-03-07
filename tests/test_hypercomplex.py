import pytest

from sounio_stroke_lab.hypercomplex import HypercomplexNumber


def test_quaternion_norm_is_multiplicative():
    left = HypercomplexNumber(1.2, -0.4, 0.8, 0.3)
    right = HypercomplexNumber(0.5, 0.7, -1.1, 0.2)

    product = left * right

    assert product.norm() == pytest.approx(left.norm() * right.norm(), rel=1e-9)


def test_quaternion_times_conjugate_collapses_to_real_norm():
    value = HypercomplexNumber(0.9, -0.1, 0.4, 1.3)

    collapsed = value * value.conjugate()

    assert collapsed.i == pytest.approx(0.0, abs=1e-9)
    assert collapsed.j == pytest.approx(0.0, abs=1e-9)
    assert collapsed.k == pytest.approx(0.0, abs=1e-9)
    assert collapsed.r == pytest.approx(value.norm() ** 2, rel=1e-9)
