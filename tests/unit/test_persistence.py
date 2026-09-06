from pathlib import Path

from logdigitizer.domain.segment import Segment
from logdigitizer.domain.states import StageStatus
from logdigitizer.io.project_store import create_project, load_project, project_to_payload, save_project


def test_save_load_round_trip_preserves_state(tmp_path: Path) -> None:
    project, paths = create_project(
        name="BH01",
        project_directory=tmp_path / "BH01_project",
        well_name="BH-01",
        location="Madrid",
        description="Round trip",
    )
    first = Segment.create(
        order=1,
        name="segment01.png",
        source_path="source/segment01.png",
        checksum="a" * 64,
    )
    first.preprocess_status = StageStatus.COMPLETE
    second = Segment.create(
        order=2,
        name="segment02.tif",
        source_path="source/segment02.tif",
        checksum="b" * 64,
    )
    project.add_segment(first)
    project.add_segment(second)
    save_project(project, paths.project_file)
    expected = project_to_payload(project)

    loaded, loaded_paths = load_project(paths.project_file)

    assert project_to_payload(loaded) == expected
    assert loaded_paths.root == paths.root
    assert [segment.segment_id for segment in loaded.segments] == [
        first.segment_id,
        second.segment_id,
    ]
    assert [segment.order for segment in loaded.segments] == [1, 2]
    assert loaded.metadata == project.metadata
    assert loaded.segments[0].checksum == "a" * 64
    assert loaded.segments[0].preprocess_status is StageStatus.COMPLETE
    assert loaded.segments[0].source_missing is True
