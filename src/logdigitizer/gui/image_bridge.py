"""Conversion helpers between QImage and NumPy without geometry logic."""

from __future__ import annotations

import numpy as np
from PySide6.QtGui import QImage


def qimage_to_numpy(image: QImage) -> np.ndarray:
    """Return a detached NumPy raster preserving common 8/16-bit QImage formats.

    Channel convention for color images is RGB/RGBA. The returned array owns its
    memory and is therefore safe after the source QImage changes.
    """
    if image.isNull():
        raise ValueError("Cannot convert a null QImage.")

    fmt = image.format()
    grayscale8 = QImage.Format.Format_Grayscale8
    indexed8 = QImage.Format.Format_Indexed8
    grayscale16 = QImage.Format.Format_Grayscale16
    rgb888 = QImage.Format.Format_RGB888
    rgba8888 = QImage.Format.Format_RGBA8888
    rgbx8888 = QImage.Format.Format_RGBX8888
    rgba64 = QImage.Format.Format_RGBA64
    rgbx64 = QImage.Format.Format_RGBX64

    if fmt in (grayscale8, indexed8):
        return _read_u8(image, channels=1)

    if fmt == grayscale16:
        return _read_u16(image, channels=1)

    if fmt == rgb888:
        return _read_u8(image, channels=3)

    if fmt in (rgba8888, rgbx8888):
        return _read_u8(image, channels=4)

    if fmt in (rgba64, rgbx64):
        return _read_u16(image, channels=4)

    # Most other QImage formats are packed or endian-dependent. Convert only
    # those unsupported display formats to an explicit RGBA byte layout.
    converted = image.convertToFormat(rgba8888)
    return _read_u8(converted, channels=4)


def numpy_to_qimage(array: np.ndarray) -> QImage:
    """Create a detached QImage from a supported NumPy raster.

    Accepted arrays:
    - HxW uint8 / uint16 grayscale
    - HxWx3 uint8 RGB
    - HxWx4 uint8 RGBA
    - HxWx4 uint16 RGBA
    """
    image = np.asarray(array)
    if image.size == 0:
        raise ValueError("Cannot convert an empty array to QImage.")

    contiguous = np.ascontiguousarray(image)

    if contiguous.ndim == 2:
        height, width = contiguous.shape
        if contiguous.dtype == np.uint8:
            fmt = QImage.Format.Format_Grayscale8
        elif contiguous.dtype == np.uint16:
            fmt = QImage.Format.Format_Grayscale16
        else:
            raise TypeError(
                "Grayscale preview arrays must use uint8 or uint16."
            )
        qimage = QImage(
            contiguous.data,
            width,
            height,
            contiguous.strides[0],
            fmt,
        )
        return qimage.copy()

    if contiguous.ndim != 3:
        raise ValueError("Image array must have 2 or 3 dimensions.")

    height, width, channels = contiguous.shape

    if channels == 3 and contiguous.dtype == np.uint8:
        fmt = QImage.Format.Format_RGB888
    elif channels == 4 and contiguous.dtype == np.uint8:
        fmt = QImage.Format.Format_RGBA8888
    elif channels == 4 and contiguous.dtype == np.uint16:
        fmt = QImage.Format.Format_RGBA64
    else:
        raise TypeError(
            "Unsupported preview array. Expected RGB uint8, RGBA uint8, "
            "or RGBA uint16."
        )

    qimage = QImage(
        contiguous.data,
        width,
        height,
        contiguous.strides[0],
        fmt,
    )
    return qimage.copy()


def _read_u8(image: QImage, channels: int) -> np.ndarray:
    width = image.width()
    height = image.height()
    row_bytes = image.bytesPerLine()

    buffer = np.frombuffer(
        image.bits(),
        dtype=np.uint8,
        count=image.sizeInBytes(),
    ).reshape(height, row_bytes)

    useful = buffer[:, : width * channels].copy()

    if channels == 1:
        return useful.reshape(height, width)

    return useful.reshape(height, width, channels)


def _read_u16(image: QImage, channels: int) -> np.ndarray:
    width = image.width()
    height = image.height()
    row_values = image.bytesPerLine() // 2

    buffer = np.frombuffer(
        image.bits(),
        dtype=np.uint16,
        count=image.sizeInBytes() // 2,
    ).reshape(height, row_values)

    useful = buffer[:, : width * channels].copy()

    if channels == 1:
        return useful.reshape(height, width)

    return useful.reshape(height, width, channels)
