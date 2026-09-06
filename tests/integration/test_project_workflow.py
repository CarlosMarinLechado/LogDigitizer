from pathlib import Path

from logdigitizer.application.project_service import ProjectService


def test_create_import_save_reload_two_segments(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    image_a = external / "scan.png"
    image_b = external / "scan.tif"
    image_a.write_bytes(b"synthetic png content A")
    image_b.write_bytes(b"synthetic tiff content B")

    project_dir = tmp_path / "BH01_project"
    service = ProjectService()
    project = service.new_project(
        name="BH01",
        project_directory=project_dir,
        well_name="BH-01",
    )
    added = service.add_images([image_a, image_b])
    assert len(added) == 2
    original_ids = [segment.segment_id for segment in project.segments]
    original_checksums = [segment.checksum for segment in project.segments]

    service.move_segment(original_ids[1], -1)
    expected_ids = [original_ids[1], original_ids[0]]
    service.save()
    project_file = service.paths.project_file

    reopened = ProjectService()
    loaded = reopened.open_project(project_file)

    assert len(loaded.segments) == 2
    assert [segment.segment_id for segment in loaded.segments] == expected_ids
    assert [segment.order for segment in loaded.segments] == [1, 2]
    assert [segment.checksum for segment in loaded.segments] == [
        original_checksums[1],
        original_checksums[0],
    ]
    assert all(segment.source_path.startswith("source/") for segment in loaded.segments)
    assert all(reopened.resolve_segment_source(segment).is_file() for segment in loaded.segments)
    assert all(not segment.source_missing for segment in loaded.segments)
