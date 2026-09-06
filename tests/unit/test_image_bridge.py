from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QImage

from logdigitizer.gui.image_bridge import (
    numpy_to_qimage,
    qimage_to_numpy,
)


@pytest.mark.parametrize(
    "array",
    [
        np.arange(30, dtype=np.uint8).reshape(5, 6),
        np.arange(30, dtype=np.uint16).reshape(5, 6),
        np.arange(5 * 6 * 3, dtype=np.uint8).reshape(5, 6, 3),
        np.arange(5 * 6 * 4, dtype=np.uint8).reshape(5, 6, 4),
        np.arange(5 * 6 * 4, dtype=np.uint16).reshape(5, 6, 4),
    ],
)
def test_numpy_qimage_round_trip_preserves_pixels(array: np.ndarray) -> None:
    qimage = numpy_to_qimage(array)
    recovered = qimage_to_numpy(qimage)

    np.testing.assert_array_equal(
        recovered,
        array,
    )


def test_null_qimage_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="null",
    ):
        qimage_to_numpy(QImage())
