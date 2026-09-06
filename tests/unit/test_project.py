from logdigitizer import PROJECT_FORMAT_VERSION, __version__
from logdigitizer.domain.project import LogProject


def test_create_project_domain() -> None:
    project = LogProject.create(
        name="BH-01",
        well_name="BH-01",
        location="Test site",
        description="Synthetic project",
    )

    assert project.name == "BH-01"
    assert project.project_id
    assert project.format_version == PROJECT_FORMAT_VERSION
    assert project.software_version == __version__
    assert project.metadata["well_name"] == "BH-01"
    assert project.metadata["location"] == "Test site"
    assert project.metadata["description"] == "Synthetic project"
    assert project.segments == []
