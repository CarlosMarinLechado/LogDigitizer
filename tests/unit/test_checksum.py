from pathlib import Path

from logdigitizer.io.image_io import calculate_sha256


def test_checksum_is_stable(tmp_path: Path) -> None:
    image = tmp_path / "synthetic.png"
    image.write_bytes(b"same synthetic image bytes")

    first = calculate_sha256(image)
    second = calculate_sha256(image)

    assert first == second
    assert len(first) == 64
