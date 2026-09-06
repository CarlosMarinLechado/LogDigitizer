"""Original-image import plus derived-raster persistence utilities."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import cv2
import numpy as np

from logdigitizer.utils.validation import (
    SourceImageError,
    UnsupportedImageFormatError,
)

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
DERIVED_IMAGE_SUFFIXES = {".png", ".tif", ".tiff"}


def validate_image_path(path: Path) -> Path:
    """Validate an existing image path and supported file extension."""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise SourceImageError(
            f"Image does not exist or is not a file: {path}"
        )
    if path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise UnsupportedImageFormatError(
            f"Unsupported image format '{path.suffix}'. "
            "Supported: PNG, JPEG and TIFF."
        )
    return path


def calculate_sha256(
    path: Path,
    chunk_size: int = 1024 * 1024,
) -> str:
    """Calculate SHA-256 efficiently using streaming chunks."""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(chunk_size):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise SourceImageError(
            f"Cannot calculate checksum for {path}: {exc}"
        ) from exc


def _unique_destination(
    source_name: str,
    destination_dir: Path,
) -> Path:
    candidate = destination_dir / source_name
    if not candidate.exists():
        return candidate

    source = Path(source_name)
    counter = 2

    while True:
        candidate = (
            destination_dir
            / f"{source.stem}_{counter}{source.suffix}"
        )
        if not candidate.exists():
            return candidate
        counter += 1


def import_original_image(
    source_path: Path,
    destination_dir: Path,
) -> tuple[Path, str]:
    """Copy an original image to ``source/`` without overwriting."""
    source_path = validate_image_path(source_path)
    destination_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    destination = _unique_destination(
        source_path.name,
        destination_dir,
    )

    try:
        shutil.copy2(
            source_path,
            destination,
        )
    except OSError as exc:
        raise SourceImageError(
            f"Cannot copy source image to {destination}: {exc}"
        ) from exc

    checksum = calculate_sha256(destination)
    return destination, checksum


def save_derived_image(
    image: np.ndarray,
    destination: Path,
) -> Path:
    """Atomically save a derived RGB/RGBA or grayscale raster.

    Color arrays use the GUI/processing convention RGB or RGBA. Conversion to
    OpenCV's BGR/BGRA convention is performed only at the I/O boundary.
    """
    destination = destination.expanduser().resolve()
    suffix = destination.suffix.lower()

    if suffix not in DERIVED_IMAGE_SUFFIXES:
        raise UnsupportedImageFormatError(
            "Derived rectification images must use PNG or TIFF."
        )

    array = np.asarray(image)

    if array.size == 0 or array.ndim not in (2, 3):
        raise SourceImageError(
            "Cannot save an empty or invalid derived image."
        )

    if array.dtype not in (np.uint8, np.uint16):
        raise SourceImageError(
            "Derived images must use uint8 or uint16 pixel data."
        )

    if array.ndim == 2:
        encoded_input = array
    elif array.shape[2] == 3:
        encoded_input = cv2.cvtColor(
            array,
            cv2.COLOR_RGB2BGR,
        )
    elif array.shape[2] == 4:
        encoded_input = cv2.cvtColor(
            array,
            cv2.COLOR_RGBA2BGRA,
        )
    else:
        raise SourceImageError(
            "Color derived images must have 3 or 4 channels."
        )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    extension = ".tiff" if suffix == ".tiff" else suffix
    success, encoded = cv2.imencode(
        extension,
        encoded_input,
    )
    if not success:
        raise SourceImageError(
            f"OpenCV could not encode derived image as {suffix}."
        )

    temporary = destination.with_suffix(
        destination.suffix + ".tmp"
    )

    try:
        temporary.write_bytes(
            encoded.tobytes()
        )
        temporary.replace(destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise SourceImageError(
                f"Cannot save derived image to {destination}: {exc}"
            ) from exc

    return destination
