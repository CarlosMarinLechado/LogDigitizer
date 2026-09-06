"""JSON persistence for versioned .logproj files."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from logdigitizer import PROJECT_FORMAT_VERSION
from logdigitizer.domain.calibration import CalibrationError, CurveDefinition
from logdigitizer.domain.digitization import DigitizedCurve, DigitizationError
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.domain.states import SegmentStatus, StageStatus
from logdigitizer.utils.paths import (
    ProjectPaths,
    create_project_directories,
    safe_project_stem,
)
from logdigitizer.utils.validation import (
    IncompatibleProjectVersionError,
    InvalidProjectError,
    ProjectNotFoundError,
    validate_new_project_directory,
    validate_project_name,
)


def create_project(
    *,
    name: str,
    project_directory: Path,
    well_name: str = "",
    location: str = "",
    description: str = "",
) -> tuple[LogProject, ProjectPaths]:
    """Create canonical directories, a domain project and its first .logproj file."""
    name = validate_project_name(name)
    root = validate_new_project_directory(project_directory)
    create_project_directories(root)
    project_file = root / f"{safe_project_stem(name)}.logproj"
    project = LogProject.create(
        name=name,
        well_name=well_name,
        location=location,
        description=description,
    )
    paths = ProjectPaths(root=root, project_file=project_file)
    save_project(project, project_file)
    return project, paths


def _segment_to_dict(segment: Segment) -> dict[str, Any]:
    data = asdict(segment)
    # Runtime availability is derived when opening the project, not persisted.
    data.pop("source_missing", None)
    data["status"] = segment.status.value
    data["preprocess_status"] = segment.preprocess_status.value
    data["calibration_status"] = segment.calibration_status.value
    data["digitization_status"] = segment.digitization_status.value
    data["digitizations"] = [
        digitized.to_dict() for digitized in segment.digitizations
    ]
    return data


def project_to_payload(project: LogProject) -> dict[str, Any]:
    """Serialize a project to deterministic, human-readable JSON-compatible data."""
    return {
        "format_version": project.format_version,
        "project": {
            "id": project.project_id,
            "name": project.name,
            "created_at": project.created_at,
            "modified_at": project.modified_at,
            "software_version": project.software_version,
            "metadata": project.metadata,
            "curves": [curve.to_dict() for curve in project.curves],
            "segments": [
                _segment_to_dict(segment)
                for segment in project.segments
            ],
        },
    }


def save_project(project: LogProject, project_file: Path) -> None:
    """Atomically write a project file as UTF-8 JSON."""
    project.touch()
    payload = project_to_payload(project)
    project_file = project_file.expanduser().resolve()
    project_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = project_file.with_suffix(project_file.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
                sort_keys=False,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(project_file)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        finally:
            raise InvalidProjectError(f"Cannot save project: {exc}") from exc



def _parse_digitizations(items: Any) -> list[DigitizedCurve]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise InvalidProjectError("Segment digitizations must be a list.")
    try:
        digitizations = [DigitizedCurve.from_dict(item) for item in items]
    except (DigitizationError, KeyError, TypeError, ValueError) as exc:
        raise InvalidProjectError(
            f"Invalid digitization entry: {exc}"
        ) from exc

    curve_ids = [item.curve_id for item in digitizations]
    if len(curve_ids) != len(set(curve_ids)):
        raise InvalidProjectError(
            "Segment contains duplicate digitized curve IDs."
        )
    return digitizations

def _parse_segment(data: dict[str, Any]) -> Segment:
    try:
        return Segment(
            segment_id=str(data["segment_id"]),
            order=int(data["order"]),
            name=str(data["name"]),
            source_path=str(data["source_path"]),
            checksum=data.get("checksum"),
            status=SegmentStatus(
                data.get("status", SegmentStatus.ACTIVE.value)
            ),
            preprocess_status=StageStatus(
                data.get(
                    "preprocess_status",
                    StageStatus.NOT_STARTED.value,
                )
            ),
            calibration_status=StageStatus(
                data.get(
                    "calibration_status",
                    StageStatus.NOT_STARTED.value,
                )
            ),
            digitization_status=StageStatus(
                data.get(
                    "digitization_status",
                    StageStatus.NOT_STARTED.value,
                )
            ),
            digitizations=_parse_digitizations(
                data.get("digitizations", [])
            ),
            source_missing=bool(data.get("source_missing", False)),
            extensions=dict(data.get("extensions", {})),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidProjectError(f"Invalid segment entry: {exc}") from exc


def _parse_curves(items: Any) -> list[CurveDefinition]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise InvalidProjectError("Project curves must be a list.")
    try:
        curves = [CurveDefinition.from_dict(item) for item in items]
    except (CalibrationError, KeyError, TypeError, ValueError) as exc:
        raise InvalidProjectError(f"Invalid curve entry: {exc}") from exc

    ids = [curve.curve_id for curve in curves]
    if len(ids) != len(set(ids)):
        raise InvalidProjectError("Project contains duplicate curve IDs.")

    mnemonics = [curve.mnemonic.casefold() for curve in curves]
    if len(mnemonics) != len(set(mnemonics)):
        raise InvalidProjectError("Project contains duplicate curve mnemonics.")

    return curves


def load_project(project_file: Path) -> tuple[LogProject, ProjectPaths]:
    """Load, validate and reconstruct a project while tolerating missing source files.

    Older projects remain loadable because missing ``curves`` and
    ``digitizations`` collections are interpreted as empty lists.
    """
    project_file = project_file.expanduser().resolve()
    if not project_file.is_file():
        raise ProjectNotFoundError(f"Project file not found: {project_file}")
    try:
        payload = json.loads(project_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InvalidProjectError(f"Cannot read project JSON: {exc}") from exc

    try:
        format_version = int(payload["format_version"])
        data = payload["project"]
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidProjectError(
            f"Missing or invalid project structure: {exc}"
        ) from exc

    if format_version != PROJECT_FORMAT_VERSION:
        raise IncompatibleProjectVersionError(
            f"Unsupported project format {format_version}; supported format is "
            f"{PROJECT_FORMAT_VERSION}."
        )

    try:
        project = LogProject(
            format_version=format_version,
            project_id=str(data["id"]),
            name=str(data["name"]),
            created_at=str(data["created_at"]),
            modified_at=str(data["modified_at"]),
            software_version=str(data["software_version"]),
            metadata=dict(data.get("metadata", {})),
            curves=_parse_curves(data.get("curves", [])),
            segments=[
                _parse_segment(item)
                for item in data.get("segments", [])
            ],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidProjectError(f"Invalid project data: {exc}") from exc

    known_curve_ids = {curve.curve_id for curve in project.curves}
    for segment in project.segments:
        unknown = [
            item.curve_id
            for item in segment.digitizations
            if item.curve_id not in known_curve_ids
        ]
        if unknown:
            raise InvalidProjectError(
                "Segment digitization references unknown project curve ID(s): "
                + ", ".join(sorted(unknown))
            )

    project.segments.sort(key=lambda item: item.order)
    for expected_order, segment in enumerate(project.segments, start=1):
        segment.order = expected_order
        resolved_source = (
            (project_file.parent / segment.source_path).resolve()
            if not Path(segment.source_path).is_absolute()
            else Path(segment.source_path)
        )
        segment.source_missing = not resolved_source.is_file()

    return project, ProjectPaths(
        root=project_file.parent,
        project_file=project_file,
    )
