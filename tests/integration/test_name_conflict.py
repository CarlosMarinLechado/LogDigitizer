from pathlib import Path

from logdigitizer.application.project_service import ProjectService


def test_same_filename_is_imported_without_overwrite(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    image_a = dir_a / "scan.png"
    image_b = dir_b / "scan.png"
    image_a.write_bytes(b"first")
    image_b.write_bytes(b"second")

    service = ProjectService()
    service.new_project(name="P", project_directory=tmp_path / "P_project")
    segments = service.add_images([image_a, image_b])

    assert segments[0].source_path == "source/scan.png"
    assert segments[1].source_path == "source/scan_2.png"
    assert service.resolve_segment_source(segments[0]).read_bytes() == b"first"
    assert service.resolve_segment_source(segments[1]).read_bytes() == b"second"
