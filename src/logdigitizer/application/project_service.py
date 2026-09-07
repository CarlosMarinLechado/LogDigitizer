"""Application-layer coordination for projects and segments."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from pathlib import Path
from uuid import uuid4

import numpy as np

from logdigitizer.domain.calibration import (
    CalibrationError,
    CurveDefinition,
    DEFAULT_HORIZONTAL_SCALE_ID,
    DepthCalibration,
    DepthCalibrationPoint,
    DepthUnit,
    HorizontalCalibration,
    HorizontalCalibrationPoint,
    HorizontalScaleProfile,
    ScaleType,
)
from logdigitizer.domain.digitization import DigitizedCurve
from logdigitizer.domain.project import LogProject
from logdigitizer.domain.segment import Segment
from logdigitizer.domain.states import (
    CurveCalibrationReadiness,
    StageStatus,
)
from logdigitizer.domain.workflow import (
    CurveWorkflowSummary,
    SegmentWorkflowSummary,
    WorkflowState,
)
from logdigitizer.io.image_io import (
    import_original_image,
    save_derived_image,
)
from logdigitizer.io.las_export import (
    DEFAULT_LAS_NULL_VALUE,
    MAX_LAS_SCALE_TRANSITION_GAP_Y_PX,
    LasExportCurveInfo,
    LasExportError,
    LasExportPlan,
    LasExportPreview,
    LasScaleTransition,
    LasWellMetadata,
    prepare_las_export as build_las_export_plan,
    preview_las_export as build_las_export_preview,
    write_las_2,
)
from logdigitizer.io.project_store import (
    create_project,
    load_project,
    save_project,
)
from logdigitizer.processing.calibration import (
    CalibrationRangeError,
    pixel_x_to_value,
    pixel_y_to_depth,
)
from logdigitizer.processing.enhancement import (
    EnhancementSettings,
    validate_enhancement_settings,
)
from logdigitizer.processing.geometry import (
    MultipointTransformResult,
    PerspectiveTransformResult,
)
from logdigitizer.processing.curve_assembly import (
    AssembledProjectCurve,
    CurveAssemblyError,
    CurveAssemblyInput,
    CurveJoin,
    build_project_curve,
)
from logdigitizer.processing.scientific_sampling import (
    DepthSamplingDefinition,
    SampledProjectCurves,
    ScientificSamplingError,
    sample_assembled_project_curves,
)
from logdigitizer.utils.logging import configure_logging, get_logger
from logdigitizer.utils.paths import (
    ProjectPaths,
    resolve_project_path,
    to_project_relative,
)

logger = get_logger("project_service")


class ImageOverlapRelation(str, Enum):
    """Neighbour represented by one calibrated image-overlap guide."""

    PREVIOUS = "previous"
    NEXT = "next"


@dataclass(frozen=True, slots=True)
class ImageOverlapGuide:
    """One overlap boundary visible inside a calibrated image."""

    depth: float
    relation: ImageOverlapRelation
    neighbour_segment_id: str
    neighbour_segment_name: str


class ProjectService:
    """Coordinate use-cases without depending on PySide6 widgets."""

    def __init__(self) -> None:
        self.project: LogProject | None = None
        self.paths: ProjectPaths | None = None
        self.active_segment_id: str | None = None
        self.dirty = False

    @property
    def has_project(self) -> bool:
        return self.project is not None and self.paths is not None

    @property
    def active_segment(self) -> Segment | None:
        if not self.project or not self.active_segment_id:
            return None
        try:
            return self.project.get_segment(
                self.active_segment_id
            )
        except KeyError:
            return None

    def new_project(
        self,
        *,
        name: str,
        project_directory: Path,
        well_name: str = "",
        location: str = "",
        description: str = "",
    ) -> LogProject:
        project, paths = create_project(
            name=name,
            project_directory=project_directory,
            well_name=well_name,
            location=location,
            description=description,
        )
        self.project = project
        self.paths = paths
        self.active_segment_id = None
        self.dirty = False
        configure_logging(paths.root)
        logger.info(
            "Created project '%s' (%s)",
            project.name,
            project.project_id,
        )
        return project

    def open_project(
        self,
        project_file: Path,
    ) -> LogProject:
        project, paths = load_project(
            project_file
        )
        self.project = project
        self.paths = paths
        self.active_segment_id = (
            project.segments[0].segment_id
            if project.segments
            else None
        )
        self.dirty = False
        configure_logging(paths.root)
        logger.info(
            "Opened project '%s' (%s)",
            project.name,
            project.project_id,
        )
        missing = sum(
            segment.source_missing
            for segment in project.segments
        )
        if missing:
            logger.warning(
                "Project contains %d missing source image(s)",
                missing,
            )
        return project

    def save(self) -> None:
        if not self.project or not self.paths:
            return
        save_project(
            self.project,
            self.paths.project_file,
        )
        self.dirty = False
        logger.info(
            "Saved project '%s'",
            self.project.name,
        )

    def add_images(
        self,
        image_paths: list[Path],
    ) -> list[Segment]:
        if not self.project or not self.paths:
            raise RuntimeError("No project is open.")

        added: list[Segment] = []

        for image_path in image_paths:
            imported_path, checksum = import_original_image(
                image_path,
                self.paths.source,
            )
            relative = to_project_relative(
                imported_path,
                self.paths.root,
            )
            segment = Segment.create(
                order=len(self.project.segments) + 1,
                name=imported_path.name,
                source_path=relative,
                checksum=checksum,
            )
            self.project.add_segment(segment)
            added.append(segment)
            logger.info(
                "Imported segment %s from %s as %s checksum=%s",
                segment.segment_id,
                image_path,
                relative,
                checksum,
            )

        if added:
            self.active_segment_id = added[-1].segment_id
            self.dirty = True

        return added

    def remove_segment(
        self,
        segment_id: str,
    ) -> Segment:
        if not self.project:
            raise RuntimeError("No project is open.")

        removed = self.project.remove_segment(
            segment_id
        )
        self._remove_calibration_marker_segment_state(segment_id)

        if self.active_segment_id == segment_id:
            self.active_segment_id = (
                self.project.segments[0].segment_id
                if self.project.segments
                else None
            )

        self.dirty = True
        logger.info(
            "Removed segment %s from project model",
            segment_id,
        )
        return removed

    def move_segment(
        self,
        segment_id: str,
        offset: int,
    ) -> bool:
        if not self.project:
            raise RuntimeError("No project is open.")

        changed = self.project.move_segment(
            segment_id,
            offset,
        )
        if changed:
            self.dirty = True
            logger.info(
                "Moved segment %s by offset %d",
                segment_id,
                offset,
            )
        return changed

    def select_segment(
        self,
        segment_id: str | None,
    ) -> Segment | None:
        if not self.project or segment_id is None:
            self.active_segment_id = None
            return None

        segment = self.project.get_segment(
            segment_id
        )
        self.active_segment_id = segment_id
        return segment

    def resolve_segment_source(
        self,
        segment: Segment | None = None,
    ) -> Path | None:
        if not self.paths:
            return None

        target = segment or self.active_segment
        if not target:
            return None

        return resolve_project_path(
            target.source_path,
            self.paths.root,
        )

    def resolve_segment_preprocessed(
        self,
        segment: Segment | None = None,
    ) -> Path | None:
        """Resolve the persisted rectified raster if one is recorded."""
        if not self.paths:
            return None

        target = segment or self.active_segment
        if target is None:
            return None

        preprocess = target.extensions.get(
            "preprocess",
            {},
        )
        stored = preprocess.get(
            "derived_path"
        )
        if not stored:
            return None

        return resolve_project_path(
            str(stored),
            self.paths.root,
        )

    def resolve_segment_working(
        self,
        segment: Segment | None = None,
    ) -> Path | None:
        """Resolve the final working raster, falling back to rectified image."""
        if not self.paths:
            return None

        target = segment or self.active_segment
        if target is None:
            return None

        preprocess = target.extensions.get(
            "preprocess",
            {},
        )

        stored = preprocess.get(
            "working_path"
        ) or preprocess.get(
            "derived_path"
        )

        if not stored:
            return None

        return resolve_project_path(
            str(stored),
            self.paths.root,
        )

    def get_enhancement_settings(
        self,
        segment: Segment | None = None,
    ) -> EnhancementSettings:
        """Restore persisted enhancement parameters or return neutral defaults."""
        target = segment or self.active_segment
        if target is None:
            return EnhancementSettings()

        preprocess = target.extensions.get(
            "preprocess",
            {},
        )
        payload = preprocess.get(
            "enhancement_settings"
        )

        if not isinstance(payload, dict):
            return EnhancementSettings()

        try:
            # Legacy CLAHE keys, if present in an older project, are ignored.
            settings = EnhancementSettings(
                black_point=int(
                    payload.get("black_point", 0)
                ),
                midtones=float(
                    payload.get("midtones", 1.0)
                ),
                white_point=int(
                    payload.get("white_point", 255)
                ),
            )
            validate_enhancement_settings(
                settings
            )
        except (
            IndexError,
            TypeError,
            ValueError,
        ) as exc:
            logger.warning(
                "Invalid persisted enhancement settings for segment %s: %s",
                target.segment_id,
                exc,
            )
            return EnhancementSettings()

        return settings

    def get_depth_calibration_unit(
        self,
        segment: Segment | None = None,
    ) -> DepthUnit:
        """Return the persisted vertical depth unit for one segment."""
        target = segment or self.active_segment
        if target is None:
            return DepthUnit.METERS

        payload = self._depth_payload(target)
        if payload is None:
            return DepthUnit.METERS

        try:
            return DepthUnit(
                str(payload.get("unit", DepthUnit.METERS.value)).upper()
            )
        except ValueError:
            logger.warning(
                "Invalid persisted depth unit for segment %s",
                target.segment_id,
            )
            return DepthUnit.METERS

    def get_depth_calibration_points(
        self,
        segment: Segment | None = None,
    ) -> tuple[DepthCalibrationPoint, ...]:
        """Return persisted depth points, including a one-point draft."""
        target = segment or self.active_segment
        if target is None:
            return ()

        payload = self._depth_payload(target)
        if payload is None:
            return ()

        raw_points = payload.get("points", [])
        if not isinstance(raw_points, list):
            logger.warning(
                "Invalid depth point payload for segment %s",
                target.segment_id,
            )
            return ()

        try:
            points = tuple(
                sorted(
                    (
                        DepthCalibrationPoint.from_dict(item)
                        for item in raw_points
                    ),
                    key=lambda point: point.pixel_y,
                )
            )
        except (CalibrationError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Invalid persisted depth calibration points for segment %s: %s",
                target.segment_id,
                exc,
            )
            return ()

        return points

    def get_depth_calibration(
        self,
        segment: Segment | None = None,
    ) -> DepthCalibration | None:
        """Return a valid scientific depth calibration when at least two points exist."""
        target = segment or self.active_segment
        if target is None:
            return None

        points = self.get_depth_calibration_points(target)
        if len(points) < 2:
            return None

        try:
            return DepthCalibration(
                points=points,
                unit=self.get_depth_calibration_unit(target),
            )
        except CalibrationError as exc:
            logger.warning(
                "Invalid persisted depth calibration for segment %s: %s",
                target.segment_id,
                exc,
            )
            return None

    def get_segment_depth_interval(
        self,
        segment: Segment | None = None,
    ) -> tuple[float, float, DepthUnit] | None:
        """Return the full image interval extrapolated to its raster edges.

        Depth-control points calibrate the vertical transform but do not
        necessarily coincide with the first and last raster rows.  When the
        rectified image height is available, the displayed project interval is
        therefore evaluated at ``pixel_y = 0`` and ``height - 1``.  Legacy
        projects without persisted raster dimensions retain the former
        control-point interval as a safe fallback.
        """
        target = segment or self.active_segment
        calibration = self.get_depth_calibration(target)
        if calibration is None:
            return None

        height = self._segment_raster_height(target)
        if height is not None:
            edge_depths = (
                self._image_edge_depth(0.0, calibration),
                self._image_edge_depth(float(height - 1), calibration),
            )
            return (
                min(edge_depths),
                max(edge_depths),
                calibration.unit,
            )
        return (
            calibration.minimum_depth,
            calibration.maximum_depth,
            calibration.unit,
        )

    @staticmethod
    def _segment_raster_height(segment: Segment | None) -> int | None:
        if segment is None:
            return None
        preprocess = segment.extensions.get("preprocess", {})
        if not isinstance(preprocess, dict):
            return None
        output_size = preprocess.get("output_size")
        if not isinstance(output_size, (list, tuple)) or len(output_size) != 2:
            return None
        try:
            height = int(output_size[1])
        except (TypeError, ValueError, OverflowError):
            return None
        return height if height > 0 else None

    @staticmethod
    def _image_edge_depth(
        pixel_y: float,
        calibration: DepthCalibration,
    ) -> float:
        try:
            return pixel_y_to_depth(pixel_y, calibration)
        except CalibrationRangeError:
            # Extrapolated rows above the physical start of the well are not
            # digitizable; the usable image envelope begins at depth zero.
            return 0.0

    def get_project_depth_intervals(
        self,
    ) -> dict[str, tuple[float, float, DepthUnit]]:
        """Return valid image intervals without assigning scientific order."""
        if self.project is None:
            return {}
        intervals: dict[str, tuple[float, float, DepthUnit]] = {}
        for segment in self.project.segments:
            interval = self.get_segment_depth_interval(segment)
            if interval is not None:
                intervals[segment.segment_id] = interval
        return intervals

    def get_image_overlap_guides(
        self,
        segment: Segment | None = None,
        curve_id: str | None = None,
    ) -> tuple[ImageOverlapGuide, ...]:
        """Return calibrated overlap boundaries for one image.

        When ``curve_id`` is supplied, only images where that project curve is
        marked as present participate in the scientific previous/next sequence.
        This allows different curve families to come from parallel image sets
        covering the same depth range without creating unrelated overlap guides.

        Images are ordered scientifically by their calibrated depth intervals,
        never by their current Project Explorer position.  A shallower image
        receives a guide at the start depth of the next relevant image; a deeper
        image receives a guide at the stop depth of the previous relevant image.
        Strict interior checks exclude touching intervals, gaps and boundaries
        that cannot be displayed inside the active image.

        Omitting ``curve_id`` preserves the historical all-calibrated-images
        behavior for compatibility with older callers/tests.
        """
        target = segment or self.active_segment
        if self.project is None or target is None:
            return ()

        if curve_id is not None:
            normalized_curve_id = str(curve_id).strip()
            if not normalized_curve_id:
                return ()
            try:
                self.project.get_curve(normalized_curve_id)
            except KeyError:
                return ()
            if not self.is_curve_present(normalized_curve_id, target):
                return ()
            curve_id = normalized_curve_id

        target_calibration = self.get_depth_calibration(target)
        target_interval = self.get_segment_depth_interval(target)
        if target_calibration is None or target_interval is None:
            return ()

        calibrated: list[tuple[float, float, str, Segment]] = []
        for candidate in self.project.segments:
            if curve_id is not None and not self.is_curve_present(
                curve_id, candidate
            ):
                continue
            interval = self.get_segment_depth_interval(candidate)
            if (
                interval is None
                or interval[2] is not target_calibration.unit
            ):
                continue
            calibrated.append(
                (
                    interval[0],
                    interval[1],
                    candidate.segment_id,
                    candidate,
                )
            )

        calibrated.sort(key=lambda item: item[:3])
        try:
            index = next(
                position
                for position, item in enumerate(calibrated)
                if item[2] == target.segment_id
            )
        except StopIteration:
            return ()

        minimum, maximum, _unit = target_interval
        guides: list[ImageOverlapGuide] = []

        if index > 0:
            previous = calibrated[index - 1]
            previous_stop = previous[1]
            if minimum < previous_stop < maximum:
                neighbour = previous[3]
                guides.append(
                    ImageOverlapGuide(
                        depth=previous_stop,
                        relation=ImageOverlapRelation.PREVIOUS,
                        neighbour_segment_id=neighbour.segment_id,
                        neighbour_segment_name=neighbour.name,
                    )
                )

        if index + 1 < len(calibrated):
            following = calibrated[index + 1]
            following_start = following[0]
            if (
                minimum < following_start < maximum
                and following[1] > maximum
            ):
                neighbour = following[3]
                guides.append(
                    ImageOverlapGuide(
                        depth=following_start,
                        relation=ImageOverlapRelation.NEXT,
                        neighbour_segment_id=neighbour.segment_id,
                        neighbour_segment_name=neighbour.name,
                    )
                )

        return tuple(guides)

    def get_las_gap_qc_excluded_intervals(
        self,
        unit: DepthUnit,
        curve_id: str | None = None,
    ) -> tuple[tuple[float, float], ...]:
        """Return exact guide-bounded overlap intervals for LAS gap QC.

        With ``curve_id`` supplied, exclusions are derived from the same
        curve-specific image sequence used by the visible red guides. Therefore
        an overlap belonging to GR cannot suppress a DT gap merely because both
        curve families occupy the same depth range. Omitting ``curve_id`` keeps
        the historical project-wide behavior for compatibility.
        """
        if self.project is None:
            return ()

        normalized_unit = (
            unit
            if isinstance(unit, DepthUnit)
            else DepthUnit(str(unit).upper())
        )
        intervals: list[tuple[float, float]] = []

        for upper in self.project.segments:
            upper_interval = self.get_segment_depth_interval(upper)
            if upper_interval is None or upper_interval[2] is not normalized_unit:
                continue

            for next_guide in self.get_image_overlap_guides(upper, curve_id):
                if next_guide.relation is not ImageOverlapRelation.NEXT:
                    continue
                try:
                    lower = self.project.get_segment(
                        next_guide.neighbour_segment_id
                    )
                except KeyError:
                    continue

                previous_guide = next(
                    (
                        guide
                        for guide in self.get_image_overlap_guides(lower, curve_id)
                        if guide.relation is ImageOverlapRelation.PREVIOUS
                        and guide.neighbour_segment_id == upper.segment_id
                    ),
                    None,
                )
                if previous_guide is None:
                    continue

                start_depth, stop_depth = sorted(
                    (float(next_guide.depth), float(previous_guide.depth))
                )
                if stop_depth > start_depth:
                    intervals.append((start_depth, stop_depth))

        intervals.sort()
        return tuple(intervals)

    def _get_las_scale_transitions(
        self,
        definitions: Sequence[CurveDefinition],
    ) -> dict[str, tuple[LasScaleTransition, ...]]:
        """Describe small intentional scale/offset seams for LAS sampling.

        This is metadata extraction only.  Saved digitization and project-wide
        curve assembly are never modified.  The LAS layer decides whether NULL
        grid samples between the two endpoints are interpolated.
        """
        if self.project is None:
            return {}

        result: dict[str, tuple[LasScaleTransition, ...]] = {}
        for curve in definitions:
            transitions: list[LasScaleTransition] = []
            for segment in self.project.segments:
                digitized = self.get_digitized_curve(curve.curve_id, segment)
                depth_calibration = self.get_depth_calibration(segment)
                if digitized is None or depth_calibration is None:
                    continue

                geometric: list[tuple[object, tuple]] = []
                for section in digitized.sections:
                    if len(section.control_points_px) >= 2:
                        points = section.control_points_px
                    elif len(section.raw_samples_px) >= 2:
                        points = section.raw_samples_px
                    else:
                        continue
                    geometric.append((section, points))

                geometric.sort(
                    key=lambda item: (
                        item[1][0].y_px,
                        item[1][-1].y_px,
                        item[0].section_id,
                    )
                )
                for (upper, upper_points), (lower, lower_points) in zip(
                    geometric, geometric[1:]
                ):
                    if (
                        upper.scale_id == lower.scale_id
                        and upper.scale_offset == lower.scale_offset
                    ):
                        continue

                    gap_y_px = lower_points[0].y_px - upper_points[-1].y_px
                    if (
                        gap_y_px <= 1e-9
                        or gap_y_px > MAX_LAS_SCALE_TRANSITION_GAP_Y_PX
                    ):
                        continue

                    upper_horizontal = self.get_horizontal_calibration(
                        curve.curve_id,
                        segment,
                        scale_id=upper.scale_id,
                    )
                    lower_horizontal = self.get_horizontal_calibration(
                        curve.curve_id,
                        segment,
                        scale_id=lower.scale_id,
                    )
                    if upper_horizontal is None or lower_horizontal is None:
                        continue

                    upper_point = upper_points[-1]
                    lower_point = lower_points[0]
                    try:
                        upper_depth = pixel_y_to_depth(
                            upper_point.y_px, depth_calibration
                        )
                        lower_depth = pixel_y_to_depth(
                            lower_point.y_px, depth_calibration
                        )
                        upper_value = pixel_x_to_value(
                            upper_point.x_px,
                            upper_horizontal,
                            scale_offset=upper.scale_offset,
                        )
                        lower_value = pixel_x_to_value(
                            lower_point.x_px,
                            lower_horizontal,
                            scale_offset=lower.scale_offset,
                        )
                    except (
                        CalibrationError,
                        CalibrationRangeError,
                        OverflowError,
                        ValueError,
                    ):
                        continue

                    if abs(float(lower_depth) - float(upper_depth)) <= 1e-12:
                        continue
                    transitions.append(
                        LasScaleTransition(
                            curve_id=curve.curve_id,
                            segment_id=segment.segment_id,
                            upper_scale_id=upper.scale_id,
                            lower_scale_id=lower.scale_id,
                            upper_scale_offset=upper.scale_offset,
                            lower_scale_offset=lower.scale_offset,
                            upper_depth=float(upper_depth),
                            lower_depth=float(lower_depth),
                            upper_value=float(upper_value),
                            lower_value=float(lower_value),
                            gap_y_px=float(gap_y_px),
                        )
                    )

            if transitions:
                result[curve.curve_id] = tuple(transitions)
        return result

    def set_depth_calibration_unit(
        self,
        unit: DepthUnit | str,
    ) -> None:
        """Set the segment depth unit before any depth points are defined."""
        segment = self._require_active_segment()
        normalized = (
            unit
            if isinstance(unit, DepthUnit)
            else DepthUnit(str(unit).upper())
        )
        if self.get_depth_calibration_points(segment):
            raise CalibrationError(
                "Clear existing depth points before changing the depth unit."
            )
        self._store_depth_calibration_points((), normalized)

    def add_depth_calibration_point(
        self,
        *,
        pixel_y: float,
        depth: float,
    ) -> tuple[DepthCalibrationPoint, ...]:
        """Add one Y/depth reference and validate as soon as two points exist."""
        segment = self._require_active_segment()
        points = list(self.get_depth_calibration_points(segment))
        points.append(DepthCalibrationPoint(float(pixel_y), float(depth)))
        return self._store_depth_calibration_points(
            points,
            self.get_depth_calibration_unit(segment),
        )

    def update_depth_calibration_point(
        self,
        index: int,
        *,
        pixel_y: float | None = None,
        depth: float | None = None,
    ) -> tuple[DepthCalibrationPoint, ...]:
        """Update one reference atomically; invalid edits leave persistence untouched."""
        segment = self._require_active_segment()
        points = list(self.get_depth_calibration_points(segment))
        if not 0 <= index < len(points):
            raise IndexError(index)

        current = points[index]
        updated = DepthCalibrationPoint(
            current.pixel_y if pixel_y is None else float(pixel_y),
            current.depth if depth is None else float(depth),
        )
        points[index] = updated
        result = self._store_depth_calibration_points(
            points,
            self.get_depth_calibration_unit(segment),
        )

        if updated.depth != current.depth:
            marker_positions = self.get_depth_calibration_marker_positions(
                segment
            )
            if current.depth in marker_positions:
                x_px = marker_positions.pop(current.depth)
                marker_positions[updated.depth] = x_px
                self.set_depth_calibration_marker_positions(
                    marker_positions,
                    segment,
                )
        return result


    def remove_depth_calibration_point(
        self,
        index: int,
    ) -> tuple[DepthCalibrationPoint, ...]:
        """Delete one depth reference while preserving the selected segment unit."""
        segment = self._require_active_segment()
        points = list(self.get_depth_calibration_points(segment))
        if not 0 <= index < len(points):
            raise IndexError(index)
        removed = points[index]
        del points[index]
        result = self._store_depth_calibration_points(
            points,
            self.get_depth_calibration_unit(segment),
        )
        marker_positions = self.get_depth_calibration_marker_positions(segment)
        if removed.depth in marker_positions:
            marker_positions.pop(removed.depth, None)
            self.set_depth_calibration_marker_positions(
                marker_positions,
                segment,
            )
        return result


    def clear_depth_calibration_points(self) -> None:
        """Clear vertical reference points without resetting the chosen unit."""
        segment = self._require_active_segment()
        self._store_depth_calibration_points(
            (),
            self.get_depth_calibration_unit(segment),
        )
        self.set_depth_calibration_marker_positions({}, segment)


    def _depth_payload(self, segment: Segment) -> dict | None:
        calibration = segment.extensions.get("calibration", {})
        if not isinstance(calibration, dict):
            return None
        payload = calibration.get("depth")
        return payload if isinstance(payload, dict) else None

    def _require_active_segment(self) -> Segment:
        if not self.project or not self.active_segment:
            raise RuntimeError("An active project segment is required.")
        return self.active_segment

    # ------------------------------------------------------------------
    # Persisted presentation state for calibration markers
    # ------------------------------------------------------------------

    @staticmethod
    def _marker_value_key(value: float) -> str:
        """Return a stable JSON-object key for one finite physical value."""
        number = float(value)
        if not isfinite(number):
            raise ValueError("Calibration marker keys must be finite.")
        return format(number, ".17g")

    def _calibration_marker_segment_payload(
        self,
        segment: Segment,
        *,
        create: bool,
    ) -> dict | None:
        """Return one segment's non-scientific calibration-marker UI payload."""
        if self.project is None:
            return None

        metadata = self.project.metadata
        ui_state = metadata.get("ui_state")
        if not isinstance(ui_state, dict):
            if not create:
                return None
            ui_state = {}
            metadata["ui_state"] = ui_state

        markers = ui_state.get("calibration_markers")
        if not isinstance(markers, dict):
            if not create:
                return None
            markers = {}
            ui_state["calibration_markers"] = markers

        payload = markers.get(segment.segment_id)
        if not isinstance(payload, dict):
            if not create:
                return None
            payload = {}
            markers[segment.segment_id] = payload
        return payload

    @staticmethod
    def _decode_marker_mapping(raw: object) -> dict[float, float]:
        if not isinstance(raw, dict):
            return {}
        decoded: dict[float, float] = {}
        for key, value in raw.items():
            try:
                physical = float(key)
                coordinate = float(value)
            except (TypeError, ValueError):
                continue
            if isfinite(physical) and isfinite(coordinate):
                decoded[physical] = coordinate
        return decoded

    @classmethod
    def _encode_marker_mapping(
        cls,
        positions: dict[float, float],
    ) -> dict[str, float]:
        encoded: dict[str, float] = {}
        for key, value in positions.items():
            physical = float(key)
            coordinate = float(value)
            if not isfinite(physical) or not isfinite(coordinate):
                raise ValueError(
                    "Calibration marker positions must contain finite values."
                )
            encoded[cls._marker_value_key(physical)] = coordinate
        return encoded

    def get_depth_calibration_marker_positions(
        self,
        segment: Segment | None = None,
    ) -> dict[float, float]:
        """Return persisted presentation X positions keyed by physical depth.

        These coordinates are UI state only. Scientific depth calibration still
        stores only ``pixel_y`` plus the physical depth value.
        """
        target = segment or self.active_segment
        if self.project is None or target is None:
            return {}
        payload = self._calibration_marker_segment_payload(
            target,
            create=False,
        )
        if not isinstance(payload, dict):
            return {}
        depth = payload.get("depth")
        if not isinstance(depth, dict):
            return {}
        return self._decode_marker_mapping(depth.get("x_by_depth"))

    def set_depth_calibration_marker_positions(
        self,
        positions: dict[float, float],
        segment: Segment | None = None,
    ) -> bool:
        """Persist presentation X positions for one image's depth T markers."""
        target = segment or self.active_segment
        if self.project is None or target is None:
            return False

        normalized = {
            float(key): float(value)
            for key, value in dict(positions).items()
        }
        encoded = self._encode_marker_mapping(normalized)
        if self.get_depth_calibration_marker_positions(target) == normalized:
            return False

        payload = self._calibration_marker_segment_payload(
            target,
            create=True,
        )
        assert payload is not None
        depth = payload.get("depth")
        if not isinstance(depth, dict):
            depth = {}

        if encoded:
            depth["x_by_depth"] = encoded
            payload["depth"] = depth
        else:
            depth.pop("x_by_depth", None)
            if depth:
                payload["depth"] = depth
            else:
                payload.pop("depth", None)

        self.project.touch()
        self.dirty = True
        return True

    def get_horizontal_calibration_marker_positions(
        self,
        curve_id: str,
        segment: Segment | None = None,
        *,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
    ) -> dict[float, float]:
        """Return persisted presentation Y positions keyed by physical X value."""
        target = segment or self.active_segment
        if self.project is None or target is None:
            return {}
        payload = self._calibration_marker_segment_payload(
            target,
            create=False,
        )
        if not isinstance(payload, dict):
            return {}
        horizontal = payload.get("horizontal")
        if not isinstance(horizontal, dict):
            return {}
        curve_payload = horizontal.get(str(curve_id))
        if not isinstance(curve_payload, dict):
            return {}
        scale_payload = curve_payload.get(str(scale_id))
        if not isinstance(scale_payload, dict):
            return {}
        return self._decode_marker_mapping(
            scale_payload.get("y_by_value")
        )

    def set_horizontal_calibration_marker_positions(
        self,
        curve_id: str,
        positions: dict[float, float],
        segment: Segment | None = None,
        *,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
    ) -> bool:
        """Persist presentation Y positions for one curve scale's T markers."""
        target = segment or self.active_segment
        if self.project is None or target is None:
            return False

        normalized = {
            float(key): float(value)
            for key, value in dict(positions).items()
        }
        encoded = self._encode_marker_mapping(normalized)
        if (
            self.get_horizontal_calibration_marker_positions(
                curve_id,
                target,
                scale_id=scale_id,
            )
            == normalized
        ):
            return False

        payload = self._calibration_marker_segment_payload(
            target,
            create=True,
        )
        assert payload is not None

        horizontal = payload.get("horizontal")
        if not isinstance(horizontal, dict):
            horizontal = {}
        curve_key = str(curve_id)
        scale_key = str(scale_id)
        curve_payload = horizontal.get(curve_key)
        if not isinstance(curve_payload, dict):
            curve_payload = {}
        scale_payload = curve_payload.get(scale_key)
        if not isinstance(scale_payload, dict):
            scale_payload = {}

        if encoded:
            scale_payload["y_by_value"] = encoded
            curve_payload[scale_key] = scale_payload
            horizontal[curve_key] = curve_payload
            payload["horizontal"] = horizontal
        else:
            scale_payload.pop("y_by_value", None)
            if scale_payload:
                curve_payload[scale_key] = scale_payload
            else:
                curve_payload.pop(scale_key, None)
            if curve_payload:
                horizontal[curve_key] = curve_payload
            else:
                horizontal.pop(curve_key, None)
            if horizontal:
                payload["horizontal"] = horizontal
            else:
                payload.pop("horizontal", None)

        self.project.touch()
        self.dirty = True
        return True

    def _remove_calibration_marker_segment_state(
        self,
        segment_id: str,
    ) -> None:
        """Drop stale marker UI state when an image is removed."""
        if self.project is None:
            return
        ui_state = self.project.metadata.get("ui_state")
        if not isinstance(ui_state, dict):
            return
        markers = ui_state.get("calibration_markers")
        if not isinstance(markers, dict):
            return
        markers.pop(str(segment_id), None)

    def _remove_horizontal_marker_curve_state(
        self,
        curve_id: str,
    ) -> None:
        """Drop stale marker UI state for a deleted global curve."""
        if self.project is None:
            return
        ui_state = self.project.metadata.get("ui_state")
        if not isinstance(ui_state, dict):
            return
        markers = ui_state.get("calibration_markers")
        if not isinstance(markers, dict):
            return
        for payload in markers.values():
            if not isinstance(payload, dict):
                continue
            horizontal = payload.get("horizontal")
            if not isinstance(horizontal, dict):
                continue
            horizontal.pop(str(curve_id), None)
            if not horizontal:
                payload.pop("horizontal", None)

    def _store_depth_calibration_points(
        self,
        points,
        unit: DepthUnit,
    ) -> tuple[DepthCalibrationPoint, ...]:
        """Validate and persist a complete or partial vertical calibration."""
        segment = self._require_active_segment()
        normalized_points = tuple(
            sorted(
                (
                    point
                    if isinstance(point, DepthCalibrationPoint)
                    else DepthCalibrationPoint(*point)
                    for point in points
                ),
                key=lambda point: point.pixel_y,
            )
        )

        if len(normalized_points) >= 2:
            calibration = DepthCalibration(
                points=normalized_points,
                unit=unit,
            )
            normalized_points = calibration.points
            depth_payload = calibration.to_dict()
        else:
            # Keep a JSON-compatible draft so the project can be saved after
            # the first click, before DepthCalibration's two-point minimum exists.
            depth_payload = {
                "unit": unit.value,
                "interpolation": "piecewise_linear",
                "extrapolation": {
                    "before_first_pixel_y": "first_segment_linear",
                    "after_last_pixel_y": "last_segment_linear",
                    "negative_depth": "forbidden",
                },
                "points": [point.to_dict() for point in normalized_points],
            }

        calibration_payload = segment.extensions.get("calibration", {})
        if not isinstance(calibration_payload, dict):
            calibration_payload = {}
        calibration_payload["depth"] = depth_payload
        segment.extensions["calibration"] = calibration_payload

        self._refresh_calibration_status(segment)
        segment.mark_downstream_review_required("calibration")

        self.project.touch()
        self.dirty = True
        logger.info(
            "Updated depth calibration for segment %s (%d point(s), unit=%s)",
            segment.segment_id,
            len(normalized_points),
            unit.value,
        )
        return normalized_points

    # ------------------------------------------------------------------
    # Project-level curve definitions and per-segment horizontal calibration
    # ------------------------------------------------------------------

    def get_curves(self) -> tuple[CurveDefinition, ...]:
        """Return the global curve definitions for the open log project."""
        if self.project is None:
            return ()
        return tuple(self.project.curves)

    def get_curve(self, curve_id: str) -> CurveDefinition:
        """Return one global curve definition by stable ID."""
        if self.project is None:
            raise RuntimeError("No project is open.")
        return self.project.get_curve(curve_id)

    def add_curve(
        self,
        *,
        name: str,
        mnemonic: str,
        unit: str,
        scale_type: ScaleType | str,
        description: str = "",
        display_color: str | None = None,
    ) -> CurveDefinition:
        """Create one curve once at project level.

        The curve identity is shared by every segment. Pixel-X calibration is
        deliberately not copied between segments.
        """
        if self.project is None:
            raise RuntimeError("No project is open.")

        curve = CurveDefinition.create(
            name=name,
            mnemonic=mnemonic,
            unit=unit,
            scale_type=scale_type,
            description=description,
            display_color=display_color,
        )
        try:
            self.project.add_curve(curve)
        except ValueError as exc:
            raise CalibrationError(str(exc)) from exc

        for segment in self.project.segments:
            self._refresh_calibration_status(segment)

        self.dirty = True
        logger.info(
            "Added project curve %s (%s)",
            curve.mnemonic,
            curve.curve_id,
        )
        return curve

    def update_curve(
        self,
        curve_id: str,
        *,
        name: str,
        mnemonic: str,
        unit: str,
        scale_type: ScaleType | str,
        description: str = "",
        display_color: str | None = None,
    ) -> CurveDefinition:
        """Edit one global curve while preserving its stable identity."""
        if self.project is None:
            raise RuntimeError("No project is open.")

        previous = self.project.get_curve(curve_id)
        candidate = CurveDefinition(
            curve_id=curve_id,
            name=name.strip(),
            mnemonic=mnemonic.strip(),
            unit=unit.strip(),
            scale_type=scale_type,
            description=description.strip(),
            display_color=(
                previous.display_color
                if display_color is None
                else display_color
            ),
        )

        # A scale-type edit changes the pixel-X -> value transform. Validate all
        # existing complete/draft points before committing the global change.
        for segment in self.project.segments:
            for profile in self.get_horizontal_scale_profiles(
                curve_id,
                segment,
            ):
                self._validate_horizontal_points_for_scale(
                    profile.points,
                    candidate.scale_type,
                )

        try:
            self.project.replace_curve(candidate)
        except ValueError as exc:
            raise CalibrationError(str(exc)) from exc

        if previous.scale_type is not candidate.scale_type:
            for segment in self.project.segments:
                if self.get_horizontal_scale_profiles(curve_id, segment):
                    segment.mark_downstream_review_required("calibration")
                self._refresh_calibration_status(segment)

        self.dirty = True
        logger.info(
            "Updated project curve %s (%s)",
            candidate.mnemonic,
            candidate.curve_id,
        )
        return candidate

    def remove_curve(self, curve_id: str) -> CurveDefinition:
        """Remove a global curve and all of its per-segment X calibrations."""
        if self.project is None:
            raise RuntimeError("No project is open.")

        try:
            curve = self.project.remove_curve(curve_id)
        except KeyError:
            raise
        self._remove_horizontal_marker_curve_state(curve_id)

        for segment in self.project.segments:
            calibration_payload = segment.extensions.get("calibration", {})
            if not isinstance(calibration_payload, dict):
                continue

            horizontal = calibration_payload.get("horizontal", {})
            had_calibration = isinstance(horizontal, dict) and curve_id in horizontal
            if isinstance(horizontal, dict):
                horizontal.pop(curve_id, None)
                if horizontal:
                    calibration_payload["horizontal"] = horizontal
                else:
                    calibration_payload.pop("horizontal", None)

            presence = calibration_payload.get("curve_presence", {})
            had_presence_override = isinstance(presence, dict) and curve_id in presence
            if isinstance(presence, dict):
                presence.pop(curve_id, None)
                if presence:
                    calibration_payload["curve_presence"] = presence
                else:
                    calibration_payload.pop("curve_presence", None)

            segment.extensions["calibration"] = calibration_payload

            try:
                segment.remove_digitized_curve(curve_id)
                had_digitization = True
            except KeyError:
                had_digitization = False

            if had_calibration or had_presence_override:
                segment.mark_downstream_review_required("calibration")
            self._refresh_calibration_status(segment)
            if had_digitization:
                self._refresh_digitization_status(segment)

        self.project.touch()
        self.dirty = True
        logger.info(
            "Removed project curve %s (%s)",
            curve.mnemonic,
            curve.curve_id,
        )
        return curve

    def is_curve_present(
        self,
        curve_id: str,
        segment: Segment | None = None,
    ) -> bool:
        """Return whether one global curve is physically present in a segment.

        Presence defaults to ``True`` for backward compatibility. Only explicit
        ``False`` overrides are persisted, so projects written before step 7.4
        naturally treat their existing curves as present.
        """
        target = segment or self.active_segment
        if target is None or self.project is None:
            return False

        self.project.get_curve(curve_id)
        calibration = target.extensions.get("calibration", {})
        if not isinstance(calibration, dict):
            return True
        presence = calibration.get("curve_presence", {})
        if not isinstance(presence, dict):
            return True
        stored = presence.get(curve_id, True)
        if isinstance(stored, bool):
            return stored
        logger.warning(
            "Invalid curve-presence flag for segment %s curve %s; assuming present",
            target.segment_id,
            curve_id,
        )
        return True

    def set_curve_present(
        self,
        curve_id: str,
        present: bool,
    ) -> bool:
        """Persist whether the selected curve exists in the active segment.

        Marking a curve absent is deliberately non-destructive: any existing X
        calibration is retained but ignored until the curve is marked present
        again. This avoids losing scientific work through a checkbox toggle.
        """
        segment = self._require_active_segment()
        self.get_curve(curve_id)
        requested = bool(present)
        current = self.is_curve_present(curve_id, segment)
        if requested == current:
            return False

        calibration = segment.extensions.get("calibration", {})
        if not isinstance(calibration, dict):
            calibration = {}
        presence = calibration.get("curve_presence", {})
        if not isinstance(presence, dict):
            presence = {}

        if requested:
            presence.pop(curve_id, None)
        else:
            presence[curve_id] = False

        if presence:
            calibration["curve_presence"] = presence
        else:
            calibration.pop("curve_presence", None)
        segment.extensions["calibration"] = calibration

        self._refresh_calibration_status(segment)
        segment.mark_downstream_review_required("calibration")
        self.project.touch()
        self.dirty = True
        logger.info(
            "Set curve presence for segment %s curve %s -> %s",
            segment.segment_id,
            curve_id,
            requested,
        )
        return True

    def get_curve_calibration_readiness(
        self,
        curve_id: str,
        segment: Segment | None = None,
    ) -> CurveCalibrationReadiness:
        """Return digitization readiness for one ``segment + curve`` pair."""
        target = segment or self.active_segment
        if target is None or self.project is None:
            raise RuntimeError("An active project segment is required.")
        self.project.get_curve(curve_id)

        if not self.is_curve_present(curve_id, target):
            return CurveCalibrationReadiness.NOT_PRESENT
        if self.get_depth_calibration(target) is None:
            return CurveCalibrationReadiness.DEPTH_NOT_CALIBRATED
        if not self.get_complete_horizontal_scale_profiles(curve_id, target):
            return CurveCalibrationReadiness.X_NOT_CALIBRATED
        return CurveCalibrationReadiness.READY

    def curve_digitization_block_reason(
        self,
        curve_id: str,
        segment: Segment | None = None,
    ) -> str | None:
        """Return a precise future-facing warning when digitization is blocked."""
        target = segment or self.active_segment
        if target is None:
            return "Select a segment before digitizing."
        curve = self.get_curve(curve_id)
        readiness = self.get_curve_calibration_readiness(curve_id, target)
        segment_label = f"Segment {target.order:02d}"

        if readiness is CurveCalibrationReadiness.READY:
            return None
        if readiness is CurveCalibrationReadiness.NOT_PRESENT:
            return (
                f"{curve.mnemonic} is marked as not present in {segment_label}."
            )
        if readiness is CurveCalibrationReadiness.DEPTH_NOT_CALIBRATED:
            return (
                f"Depth is not calibrated in {segment_label}. "
                "Calibrate depth before digitizing this curve."
            )
        return (
            f"{curve.mnemonic} is not calibrated in {segment_label}. "
            "Calibrate the curve before digitizing this segment."
        )

    def get_horizontal_calibration_points(
        self,
        curve_id: str,
        segment: Segment | None = None,
        *,
        scale_id: str | None = None,
    ) -> tuple[HorizontalCalibrationPoint, ...]:
        """Return the 0, 1 or 2 references of one horizontal scale."""
        profile = self.get_horizontal_scale_profile(
            curve_id,
            scale_id,
            segment,
        )
        return () if profile is None else profile.points

    def get_horizontal_scale_profiles(
        self,
        curve_id: str,
        segment: Segment | None = None,
    ) -> tuple[HorizontalScaleProfile, ...]:
        """Return all persisted X-scale profiles for one curve/segment.

        The first profile is decoded from the original ``points`` payload and
        receives the stable ``default`` identifier.  This keeps old project
        files valid and also preserves their exact serialized representation
        while no additional scale exists.
        """
        target = segment or self.active_segment
        if target is None:
            return ()
        if self.project is None:
            return ()

        # Ensure stale payloads cannot masquerade as a curve that no longer
        # exists globally.
        try:
            self.project.get_curve(curve_id)
        except KeyError:
            return ()

        payload = self._horizontal_payload(target, curve_id)
        if payload is None:
            return ()

        raw_points = payload.get("points", [])
        raw_additional = payload.get("additional_scales", [])
        if not isinstance(raw_points, list) or not isinstance(
            raw_additional,
            list,
        ):
            logger.warning(
                "Invalid horizontal scale payload for segment %s curve %s",
                target.segment_id,
                curve_id,
            )
            return ()
        try:
            default_points = tuple(
                sorted(
                    (
                        HorizontalCalibrationPoint.from_dict(item)
                        for item in raw_points
                    ),
                    key=lambda point: point.pixel_x,
                )
            )
            curve = self.project.get_curve(curve_id)
            self._validate_horizontal_points_for_scale(
                default_points,
                curve.scale_type,
            )

            profiles: list[HorizontalScaleProfile] = []
            # A payload with additional profiles still owns a default profile,
            # even if that profile is temporarily being recalibrated.
            if "points" in payload or raw_additional:
                profiles.append(
                    HorizontalScaleProfile(
                        DEFAULT_HORIZONTAL_SCALE_ID,
                        default_points,
                    )
                )

            for item in raw_additional:
                if not isinstance(item, dict):
                    raise CalibrationError(
                        "Additional horizontal scales must be objects."
                    )
                profile = HorizontalScaleProfile.from_dict(item)
                if profile.scale_id == DEFAULT_HORIZONTAL_SCALE_ID:
                    raise CalibrationError(
                        "Additional scales cannot use the default id."
                    )
                self._validate_horizontal_points_for_scale(
                    profile.points,
                    curve.scale_type,
                )
                profiles.append(profile)

            identifiers = [profile.scale_id for profile in profiles]
            if len(identifiers) != len(set(identifiers)):
                raise CalibrationError(
                    "Horizontal scale identifiers must be unique."
                )
        except (CalibrationError, KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Invalid horizontal scales for segment %s curve %s: %s",
                target.segment_id,
                curve_id,
                exc,
            )
            return ()
        return tuple(profiles)

    def get_horizontal_scale_profile(
        self,
        curve_id: str,
        scale_id: str | None = None,
        segment: Segment | None = None,
    ) -> HorizontalScaleProfile | None:
        """Return one horizontal scale profile, defaulting to the first."""
        profiles = self.get_horizontal_scale_profiles(curve_id, segment)
        if not profiles:
            return None
        requested = (
            profiles[0].scale_id
            if scale_id is None
            else str(scale_id).strip()
        )
        return next(
            (
                profile
                for profile in profiles
                if profile.scale_id == requested
            ),
            None,
        )

    def get_complete_horizontal_scale_profiles(
        self,
        curve_id: str,
        segment: Segment | None = None,
    ) -> tuple[HorizontalScaleProfile, ...]:
        """Return only scales that can convert pixels to curve values."""
        return tuple(
            profile
            for profile in self.get_horizontal_scale_profiles(
                curve_id,
                segment,
            )
            if profile.complete
        )

    def get_horizontal_calibration(
        self,
        curve_id: str,
        segment: Segment | None = None,
        *,
        scale_id: str | None = None,
    ) -> HorizontalCalibration | None:
        """Build the scientific two-point X calibration when it is complete."""
        target = segment or self.active_segment
        if target is None or self.project is None:
            return None
        points = self.get_horizontal_calibration_points(
            curve_id,
            target,
            scale_id=scale_id,
        )
        if len(points) != 2:
            return None
        curve = self.project.get_curve(curve_id)
        try:
            return HorizontalCalibration.from_points(
                (points[0], points[1]),
                curve.scale_type,
            )
        except CalibrationError as exc:
            logger.warning(
                "Invalid complete horizontal calibration for segment %s curve %s: %s",
                target.segment_id,
                curve_id,
                exc,
            )
            return None

    def add_horizontal_calibration_point(
        self,
        curve_id: str,
        *,
        pixel_x: float,
        value: float,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
    ) -> tuple[HorizontalCalibrationPoint, ...]:
        """Add one X/value reference; no more than two are permitted."""
        segment = self._require_active_segment()
        curve = self.get_curve(curve_id)
        points = list(
            self.get_horizontal_calibration_points(
                curve_id,
                segment,
                scale_id=scale_id,
            )
        )
        if (
            scale_id != DEFAULT_HORIZONTAL_SCALE_ID
            and self.get_horizontal_scale_profile(
                curve_id,
                scale_id,
                segment,
            )
            is None
        ):
            raise KeyError(scale_id)
        if len(points) >= 2:
            raise CalibrationError(
                "Horizontal calibration already has two points."
            )
        points.append(
            HorizontalCalibrationPoint(float(pixel_x), float(value))
        )
        return self._store_horizontal_calibration_points(
            curve,
            points,
            scale_id=scale_id,
        )

    def update_horizontal_calibration_point(
        self,
        curve_id: str,
        index: int,
        *,
        pixel_x: float | None = None,
        value: float | None = None,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
    ) -> tuple[HorizontalCalibrationPoint, ...]:
        """Atomically edit one X reference; invalid edits do not persist."""
        segment = self._require_active_segment()
        curve = self.get_curve(curve_id)
        points = list(
            self.get_horizontal_calibration_points(
                curve_id,
                segment,
                scale_id=scale_id,
            )
        )
        if not 0 <= index < len(points):
            raise IndexError(index)
        current = points[index]
        updated = HorizontalCalibrationPoint(
            current.pixel_x if pixel_x is None else float(pixel_x),
            current.value if value is None else float(value),
        )
        points[index] = updated
        result = self._store_horizontal_calibration_points(
            curve,
            points,
            scale_id=scale_id,
        )

        if updated.value != current.value:
            marker_positions = (
                self.get_horizontal_calibration_marker_positions(
                    curve_id,
                    segment,
                    scale_id=scale_id,
                )
            )
            if current.value in marker_positions:
                y_px = marker_positions.pop(current.value)
                marker_positions[updated.value] = y_px
                self.set_horizontal_calibration_marker_positions(
                    curve_id,
                    marker_positions,
                    segment,
                    scale_id=scale_id,
                )
        return result


    def remove_horizontal_calibration_point(
        self,
        curve_id: str,
        index: int,
        *,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
    ) -> tuple[HorizontalCalibrationPoint, ...]:
        """Delete one X/value reference for the active segment."""
        segment = self._require_active_segment()
        curve = self.get_curve(curve_id)
        points = list(
            self.get_horizontal_calibration_points(
                curve_id,
                segment,
                scale_id=scale_id,
            )
        )
        if not 0 <= index < len(points):
            raise IndexError(index)
        removed = points[index]
        del points[index]
        result = self._store_horizontal_calibration_points(
            curve,
            points,
            scale_id=scale_id,
        )
        marker_positions = self.get_horizontal_calibration_marker_positions(
            curve_id,
            segment,
            scale_id=scale_id,
        )
        if removed.value in marker_positions:
            marker_positions.pop(removed.value, None)
            self.set_horizontal_calibration_marker_positions(
                curve_id,
                marker_positions,
                segment,
                scale_id=scale_id,
            )
        return result


    def clear_horizontal_calibration_points(
        self,
        curve_id: str,
        *,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
    ) -> None:
        """Clear X references for one curve in the active segment only."""
        curve = self.get_curve(curve_id)
        segment = self._require_active_segment()
        self._store_horizontal_calibration_points(
            curve,
            (),
            scale_id=scale_id,
        )
        self.set_horizontal_calibration_marker_positions(
            curve_id,
            {},
            segment,
            scale_id=scale_id,
        )


    def add_horizontal_scale(self, curve_id: str) -> HorizontalScaleProfile:
        """Create one empty additional scale after existing scales are valid."""
        curve = self.get_curve(curve_id)
        self._require_active_segment()
        profiles = list(self.get_horizontal_scale_profiles(curve_id))
        if not profiles or any(not profile.complete for profile in profiles):
            raise CalibrationError(
                "Complete the current scale before adding another scale."
            )
        profile = HorizontalScaleProfile(str(uuid4()), ())
        profiles.append(profile)
        self._store_horizontal_scale_profiles(curve, profiles)
        return profile

    def remove_horizontal_scale(
        self,
        curve_id: str,
        scale_id: str,
    ) -> HorizontalScaleProfile:
        """Remove one unused additional scale from the active segment."""
        requested = str(scale_id).strip()
        if requested == DEFAULT_HORIZONTAL_SCALE_ID:
            raise CalibrationError(
                "The first scale cannot be removed; remove the curve instead."
            )
        curve = self.get_curve(curve_id)
        segment = self._require_active_segment()
        profiles = list(self.get_horizontal_scale_profiles(curve_id, segment))
        index = next(
            (
                position
                for position, profile in enumerate(profiles)
                if profile.scale_id == requested
            ),
            None,
        )
        if index is None:
            raise KeyError(requested)
        digitized = self.get_digitized_curve(curve_id, segment)
        if digitized is not None and any(
            section.scale_id == requested
            for section in digitized.sections
        ):
            raise CalibrationError(
                "This scale is used by digitized sections and cannot be removed."
            )
        removed = profiles.pop(index)
        self._store_horizontal_scale_profiles(curve, profiles)
        self.set_horizontal_calibration_marker_positions(
            curve_id,
            {},
            segment,
            scale_id=requested,
        )
        return removed

    def _horizontal_payload(
        self,
        segment: Segment,
        curve_id: str,
    ) -> dict | None:
        calibration = segment.extensions.get("calibration", {})
        if not isinstance(calibration, dict):
            return None
        horizontal = calibration.get("horizontal", {})
        if not isinstance(horizontal, dict):
            return None
        payload = horizontal.get(curve_id)
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _validate_horizontal_points_for_scale(
        points,
        scale_type: ScaleType,
    ) -> None:
        normalized = tuple(points)
        if len(normalized) > 2:
            raise CalibrationError(
                "Horizontal calibration supports exactly two points."
            )
        pixels = [point.pixel_x for point in normalized]
        if len(pixels) != len(set(pixels)):
            raise CalibrationError(
                "Horizontal calibration cannot contain duplicate pixel_x values."
            )
        if scale_type is ScaleType.LOGARITHMIC and any(
            point.value <= 0 for point in normalized
        ):
            raise CalibrationError(
                "Logarithmic calibration requires positive physical values."
            )
        if len(normalized) == 2:
            HorizontalCalibration.from_points(
                (normalized[0], normalized[1]),
                scale_type,
            )

    def _store_horizontal_calibration_points(
        self,
        curve: CurveDefinition,
        points,
        *,
        scale_id: str = DEFAULT_HORIZONTAL_SCALE_ID,
    ) -> tuple[HorizontalCalibrationPoint, ...]:
        """Validate and persist a complete or one-point horizontal draft."""
        segment = self._require_active_segment()
        normalized_points = tuple(
            sorted(
                (
                    point
                    if isinstance(point, HorizontalCalibrationPoint)
                    else HorizontalCalibrationPoint(*point)
                    for point in points
                ),
                key=lambda point: point.pixel_x,
            )
        )
        self._validate_horizontal_points_for_scale(
            normalized_points,
            curve.scale_type,
        )

        profiles = list(
            self.get_horizontal_scale_profiles(curve.curve_id, segment)
        )
        requested = str(scale_id).strip()
        if not requested:
            raise CalibrationError("Horizontal scale id cannot be empty.")
        profile = HorizontalScaleProfile(requested, normalized_points)
        existing_index = next(
            (
                index
                for index, existing in enumerate(profiles)
                if existing.scale_id == requested
            ),
            None,
        )
        if existing_index is None:
            if requested != DEFAULT_HORIZONTAL_SCALE_ID:
                raise KeyError(requested)
            profiles.insert(0, profile)
        else:
            profiles[existing_index] = profile

        # Preserve legacy clearing behaviour when the default scale is the only
        # profile. Empty additional profiles are intentional calibration drafts.
        if (
            len(profiles) == 1
            and profiles[0].scale_id == DEFAULT_HORIZONTAL_SCALE_ID
            and not profiles[0].points
        ):
            profiles = []

        self._store_horizontal_scale_profiles(curve, profiles)
        return normalized_points

    def _store_horizontal_scale_profiles(
        self,
        curve: CurveDefinition,
        profiles,
    ) -> tuple[HorizontalScaleProfile, ...]:
        """Validate and persist all scale profiles without changing legacy X."""
        segment = self._require_active_segment()
        normalized_profiles = tuple(
            profile
            if isinstance(profile, HorizontalScaleProfile)
            else HorizontalScaleProfile.from_dict(profile)
            for profile in profiles
        )
        identifiers = [profile.scale_id for profile in normalized_profiles]
        if len(identifiers) != len(set(identifiers)):
            raise CalibrationError(
                "Horizontal scale identifiers must be unique."
            )
        if normalized_profiles and (
            normalized_profiles[0].scale_id
            != DEFAULT_HORIZONTAL_SCALE_ID
        ):
            raise CalibrationError(
                "The first horizontal scale must use the default id."
            )
        for profile in normalized_profiles:
            self._validate_horizontal_points_for_scale(
                profile.points,
                curve.scale_type,
            )

        calibration_payload = segment.extensions.get("calibration", {})
        if not isinstance(calibration_payload, dict):
            calibration_payload = {}
        horizontal = calibration_payload.get("horizontal", {})
        if not isinstance(horizontal, dict):
            horizontal = {}

        if normalized_profiles:
            payload = {
                "points": [
                    point.to_dict()
                    for point in normalized_profiles[0].points
                ],
            }
            additional = [
                profile.to_dict()
                for profile in normalized_profiles[1:]
            ]
            if additional:
                payload["additional_scales"] = additional
            horizontal[curve.curve_id] = payload
        else:
            horizontal.pop(curve.curve_id, None)

        if horizontal:
            calibration_payload["horizontal"] = horizontal
        else:
            calibration_payload.pop("horizontal", None)
        segment.extensions["calibration"] = calibration_payload

        self._refresh_calibration_status(segment)
        segment.mark_downstream_review_required("calibration")
        self.project.touch()
        self.dirty = True
        logger.info(
            "Updated horizontal calibration for segment %s curve %s (%d point(s))",
            segment.segment_id,
            curve.curve_id,
            sum(len(profile.points) for profile in normalized_profiles),
        )
        return normalized_profiles

    def _refresh_calibration_status(self, segment: Segment) -> None:
        """Derive a segment summary while respecting absent project curves.

        Readiness for actual work is evaluated per curve with
        :meth:`get_curve_calibration_readiness`. The segment-level status remains
        only a compact summary and never blocks a ready curve from digitization.
        """
        if self.project is None:
            return

        depth_points = self.get_depth_calibration_points(segment)
        depth_complete = self.get_depth_calibration(segment) is not None

        horizontal_point_count = 0
        required_curves = [
            curve
            for curve in self.project.curves
            if self.is_curve_present(curve.curve_id, segment)
        ]
        horizontal_complete = all(
            bool(profiles)
            and all(profile.complete for profile in profiles)
            for curve in required_curves
            for profiles in (
                self.get_horizontal_scale_profiles(curve.curve_id, segment),
            )
        )

        # Existing X data for an explicitly absent curve is retained
        # non-destructively, but it does not count as a calibration requirement.
        for curve in self.project.curves:
            horizontal_point_count += sum(
                len(profile.points)
                for profile in self.get_horizontal_scale_profiles(
                    curve.curve_id,
                    segment,
                )
            )

        calibration_payload = segment.extensions.get("calibration", {})
        presence_overrides = (
            calibration_payload.get("curve_presence", {})
            if isinstance(calibration_payload, dict)
            else {}
        )
        has_presence_decision = bool(
            presence_overrides if isinstance(presence_overrides, dict) else {}
        )

        if self.project.curves and depth_complete and horizontal_complete:
            segment.calibration_status = StageStatus.COMPLETE
        elif depth_points or horizontal_point_count or has_presence_decision:
            segment.calibration_status = StageStatus.IN_PROGRESS
        else:
            segment.calibration_status = StageStatus.NOT_STARTED

    # ------------------------------------------------------------------
    # Derived project-tree workflow summaries
    # ------------------------------------------------------------------

    def get_segment_workflow_summary(
        self,
        segment: Segment | None = None,
    ) -> SegmentWorkflowSummary:
        """Calculate the current workflow state for one segment.

        Completion follows the project-tree rules agreed for step 8.5:
        - Preprocessing is complete once rectification is applied. Image
          Enhancement is optional.
        - Calibration requires a valid Y calibration and a valid X calibration
          for every curve marked present in the segment.
        - Digitization requires at least one saved section for every present
          curve.
        - At least one curve must be present before the segment can be complete.

        The result is derived on demand and is never persisted as a second
        source of truth.
        """
        if self.project is None:
            raise RuntimeError("No project is open.")

        target = segment or self.active_segment
        if target is None:
            raise RuntimeError("A project segment is required.")

        source_path = self.resolve_segment_source(target)
        source_missing = bool(
            target.source_missing
            or source_path is None
            or not source_path.is_file()
        )

        preprocess = target.extensions.get("preprocess", {})
        if not isinstance(preprocess, dict):
            preprocess = {}
        rectified_recorded = bool(preprocess.get("derived_path"))
        rectified_path = self.resolve_segment_preprocessed(target)

        if (
            rectified_recorded
            and rectified_path is not None
            and rectified_path.is_file()
        ):
            preprocessing_state = WorkflowState.COMPLETE
        elif rectified_recorded or source_missing:
            # A persisted rectification reference whose file disappeared is a
            # real project error, not merely an unfinished stage.
            preprocessing_state = WorkflowState.ERROR
        else:
            preprocessing_state = WorkflowState.NOT_STARTED

        depth_points = self.get_depth_calibration_points(target)
        depth_valid = self.get_depth_calibration(target) is not None
        if depth_valid:
            depth_state = WorkflowState.COMPLETE
        elif depth_points:
            depth_state = WorkflowState.INCOMPLETE
        else:
            depth_state = WorkflowState.NOT_STARTED

        curve_summaries: list[CurveWorkflowSummary] = []
        for curve in self.project.curves:
            if not self.is_curve_present(curve.curve_id, target):
                continue

            x_profiles = self.get_horizontal_scale_profiles(
                curve.curve_id,
                target,
            )
            x_point_count = sum(
                len(profile.points) for profile in x_profiles
            )
            x_valid = bool(x_profiles) and all(
                profile.complete for profile in x_profiles
            )
            if x_valid:
                x_state = WorkflowState.COMPLETE
            elif x_profiles:
                x_state = WorkflowState.INCOMPLETE
            else:
                x_state = WorkflowState.NOT_STARTED

            digitized = self.get_digitized_curve(
                curve.curve_id,
                target,
            )
            section_count = (
                len(digitized.sections)
                if digitized is not None
                else 0
            )
            digitization_state = (
                WorkflowState.COMPLETE
                if section_count >= 1
                else WorkflowState.NOT_STARTED
            )

            if (
                x_state is WorkflowState.COMPLETE
                and digitization_state is WorkflowState.COMPLETE
            ):
                curve_state = WorkflowState.COMPLETE
            elif (
                x_state is WorkflowState.NOT_STARTED
                and digitization_state is WorkflowState.NOT_STARTED
            ):
                curve_state = WorkflowState.NOT_STARTED
            else:
                curve_state = WorkflowState.INCOMPLETE

            curve_summaries.append(
                CurveWorkflowSummary(
                    curve_id=curve.curve_id,
                    mnemonic=curve.mnemonic,
                    name=curve.name,
                    unit=curve.unit,
                    display_color=curve.display_color,
                    x_state=x_state,
                    x_point_count=x_point_count,
                    digitization_state=digitization_state,
                    section_count=section_count,
                    state=curve_state,
                )
            )

        has_present_curves = bool(curve_summaries)
        all_x_valid = has_present_curves and all(
            curve.x_state is WorkflowState.COMPLETE
            for curve in curve_summaries
        )
        all_digitized = has_present_curves and all(
            curve.digitization_state is WorkflowState.COMPLETE
            for curve in curve_summaries
        )

        complete = bool(
            preprocessing_state is WorkflowState.COMPLETE
            and depth_state is WorkflowState.COMPLETE
            and all_x_valid
            and all_digitized
        )

        started = bool(
            preprocessing_state is not WorkflowState.NOT_STARTED
            or depth_state is not WorkflowState.NOT_STARTED
            or any(
                curve.state is not WorkflowState.NOT_STARTED
                for curve in curve_summaries
            )
        )

        if (
            source_missing
            or preprocessing_state is WorkflowState.ERROR
        ):
            state = WorkflowState.ERROR
        elif complete:
            state = WorkflowState.COMPLETE
        elif started:
            state = WorkflowState.INCOMPLETE
        else:
            state = WorkflowState.NOT_STARTED

        return SegmentWorkflowSummary(
            segment_id=target.segment_id,
            state=state,
            preprocessing_state=preprocessing_state,
            depth_state=depth_state,
            depth_point_count=len(depth_points),
            depth_unit=self.get_depth_calibration_unit(target),
            curves=tuple(curve_summaries),
            source_missing=source_missing,
        )

    def get_project_workflow_summaries(
        self,
    ) -> tuple[SegmentWorkflowSummary, ...]:
        """Return current workflow summaries in segment order."""
        if self.project is None:
            return ()
        return tuple(
            self.get_segment_workflow_summary(segment)
            for segment in sorted(
                self.project.segments,
                key=lambda item: item.order,
            )
        )

    # ------------------------------------------------------------------
    # Per-segment digitized curve data
    # ------------------------------------------------------------------

    def get_digitized_curve(
        self,
        curve_id: str,
        segment: Segment | None = None,
    ) -> DigitizedCurve | None:
        """Return persisted digitization for one ``segment + curve`` pair."""
        target = segment or self.active_segment
        if target is None or self.project is None:
            return None
        self.project.get_curve(curve_id)
        try:
            return target.get_digitized_curve(curve_id)
        except KeyError:
            return None

    def get_project_curve_assembly(
        self,
        curve_id: str,
        *,
        require_complete_values: bool = True,
    ) -> AssembledProjectCurve:
        """Build the final curve using calibrated depth, never GUI list order."""
        if self.project is None:
            raise RuntimeError("No project is open.")
        self.project.get_curve(curve_id)

        inputs: list[CurveAssemblyInput] = []
        for segment in self.project.segments:
            if not self.is_curve_present(curve_id, segment):
                continue
            digitized = self.get_digitized_curve(curve_id, segment)
            if digitized is None:
                continue

            depth_calibration = self.get_depth_calibration(segment)
            if depth_calibration is None:
                if require_complete_values:
                    raise CurveAssemblyError(
                        f"Depth is not calibrated in {segment.name}."
                    )
                continue

            horizontal: dict[str, HorizontalCalibration] = {}
            if require_complete_values:
                for scale_id in {
                    section.scale_id for section in digitized.sections
                }:
                    calibration = self.get_horizontal_calibration(
                        curve_id,
                        segment,
                        scale_id=scale_id,
                    )
                    if calibration is not None:
                        horizontal[scale_id] = calibration

            inputs.append(
                CurveAssemblyInput(
                    segment_id=segment.segment_id,
                    segment_name=segment.name,
                    depth_calibration=depth_calibration,
                    digitized_curve=digitized,
                    horizontal_calibrations=horizontal,
                )
            )

        return build_project_curve(
            curve_id,
            inputs,
            require_complete_values=require_complete_values,
        )

    def get_project_curve_joins(
        self,
        curve_id: str,
    ) -> tuple[CurveJoin, ...]:
        """Return internal midpoint joins without converting LAS values."""
        return self.get_project_curve_assembly(
            curve_id,
            require_complete_values=False,
        ).joins

    def get_first_image_vertical_resolution(
        self,
        depth_unit: DepthUnit | None = None,
    ) -> float | None:
        """Return the average depth represented by one pixel in image one.

        "Image one" is the shallowest calibrated image, not the first item in
        Project Explorer.  Only its two outer calibration points are used so
        this remains an intentionally simple export-orientation metric.
        """
        if self.project is None:
            raise RuntimeError("No project is open.")

        calibrated: list[tuple[float, float, str, DepthCalibration]] = []
        for segment in self.project.segments:
            calibration = self.get_depth_calibration(segment)
            if calibration is None:
                continue
            if depth_unit is not None and calibration.unit is not depth_unit:
                continue
            calibrated.append(
                (
                    calibration.minimum_depth,
                    calibration.maximum_depth,
                    segment.segment_id,
                    calibration,
                )
            )
        if not calibrated:
            return None

        calibration = min(calibrated, key=lambda item: item[:3])[3]
        first = calibration.points[0]
        last = calibration.points[-1]
        pixel_span = abs(last.pixel_y - first.pixel_y)
        if pixel_span == 0.0:
            return None
        return abs(last.depth - first.depth) / pixel_span

    def get_las_metadata(self) -> LasWellMetadata:
        """Return persisted LAS metadata, prefilled from the base project."""
        if self.project is None:
            raise RuntimeError("No project is open.")
        return LasWellMetadata.from_project_metadata(
            project_name=self.project.name,
            metadata=self.project.metadata,
        )

    def set_las_metadata(self, metadata: LasWellMetadata) -> None:
        """Persist editable LAS fields without changing their Unicode text."""
        if self.project is None:
            raise RuntimeError("No project is open.")
        payload = metadata.to_dict()
        if self.project.metadata.get("las") == payload:
            return
        self.project.metadata["las"] = payload
        self.project.touch()
        self.dirty = True

    def get_last_las_step(self) -> float | None:
        """Return the last successfully exported STEP stored in the project."""
        if self.project is None:
            raise RuntimeError("No project is open.")
        settings = self.project.metadata.get("las_export", {})
        if not isinstance(settings, dict):
            return None
        try:
            step = float(settings.get("last_step_depth"))
        except (TypeError, ValueError):
            return None
        return step if isfinite(step) and step > 0.0 else None

    def set_last_las_step(self, step_depth: float) -> None:
        """Persist STEP only after a successful export."""
        if self.project is None:
            raise RuntimeError("No project is open.")
        try:
            step = float(step_depth)
        except (TypeError, ValueError) as exc:
            raise LasExportError("LAS STEP must be a finite number.") from exc
        if not isfinite(step) or step <= 0.0:
            raise LasExportError("LAS STEP must be greater than zero.")

        current = self.project.metadata.get("las_export", {})
        settings = dict(current) if isinstance(current, dict) else {}
        if settings.get("last_step_depth") == step:
            return
        settings["last_step_depth"] = step
        self.project.metadata["las_export"] = settings
        self.project.touch()
        self.dirty = True

    def get_las_export_curve_infos(self) -> tuple[LasExportCurveInfo, ...]:
        """Describe every curve for the export selector without sampling it."""
        if self.project is None:
            return ()

        infos: list[LasExportCurveInfo] = []
        for curve in self.project.curves:
            try:
                assembly = self.get_project_curve_assembly(curve.curve_id)
            except CurveAssemblyError as exc:
                infos.append(
                    LasExportCurveInfo(
                        curve_id=curve.curve_id,
                        mnemonic=curve.mnemonic,
                        name=curve.name,
                        unit=curve.unit,
                        minimum_depth=None,
                        maximum_depth=None,
                        depth_unit=None,
                        exportable=False,
                        unavailable_reason=str(exc),
                    )
                )
                continue

            exportable = bool(assembly.sources)
            infos.append(
                LasExportCurveInfo(
                    curve_id=curve.curve_id,
                    mnemonic=curve.mnemonic,
                    name=curve.name,
                    unit=curve.unit,
                    minimum_depth=assembly.minimum_depth,
                    maximum_depth=assembly.maximum_depth,
                    depth_unit=assembly.unit,
                    exportable=exportable,
                    unavailable_reason=(
                        ""
                        if exportable
                        else "No usable digitized data."
                    ),
                )
            )
        return tuple(infos)

    def can_export_las(self) -> bool:
        """Return whether the project contains at least one exportable curve."""
        return any(info.exportable for info in self.get_las_export_curve_infos())

    def preview_las_export(
        self,
        *,
        step_depth: float,
        curve_ids: Sequence[str],
    ) -> LasExportPreview:
        """Calculate exact LAS limits and rows without curve resampling."""
        assemblies, definitions, resolution = self._las_export_inputs(
            curve_ids,
        )
        selected_units = {
            assembly.unit
            for assembly in assemblies.values()
            if assembly.unit is not None
        }
        excluded_by_curve = (
            {
                curve.curve_id: self.get_las_gap_qc_excluded_intervals(
                    next(iter(selected_units)),
                    curve.curve_id,
                )
                for curve in definitions
            }
            if len(selected_units) == 1
            else {}
        )
        scale_transitions = self._get_las_scale_transitions(definitions)
        return build_las_export_preview(
            assemblies,
            definitions,
            step_depth,
            first_image_resolution=resolution,
            gap_qc_excluded_intervals_by_curve=excluded_by_curve,
            scale_transitions=scale_transitions,
        )

    def prepare_las_export(
        self,
        *,
        step_depth: float,
        curve_ids: Sequence[str] | None = None,
        null_value: float = DEFAULT_LAS_NULL_VALUE,
    ) -> LasExportPlan:
        """Build a sampled LAS plan without formatting or writing a file.

        When no explicit selection is supplied, every project curve containing
        usable assembled data is selected in project order.
        """
        assemblies, definitions, resolution = self._las_export_inputs(
            curve_ids,
        )

        selected_units = {
            assembly.unit
            for assembly in assemblies.values()
            if assembly.unit is not None
        }
        excluded_by_curve = (
            {
                curve.curve_id: self.get_las_gap_qc_excluded_intervals(
                    next(iter(selected_units)),
                    curve.curve_id,
                )
                for curve in definitions
            }
            if len(selected_units) == 1
            else {}
        )
        scale_transitions = self._get_las_scale_transitions(definitions)
        return build_las_export_plan(
            assemblies,
            definitions,
            step_depth=step_depth,
            null_value=null_value,
            first_image_resolution=resolution,
            gap_qc_excluded_intervals_by_curve=excluded_by_curve,
            scale_transitions=scale_transitions,
        )

    def _las_export_inputs(
        self,
        curve_ids: Sequence[str] | None,
    ) -> tuple[
        dict[str, AssembledProjectCurve],
        tuple[CurveDefinition, ...],
        float | None,
    ]:
        if self.project is None:
            raise RuntimeError("No project is open.")

        if curve_ids is None:
            candidate_definitions = tuple(self.project.curves)
        else:
            normalized = tuple(str(curve_id).strip() for curve_id in curve_ids)
            if any(not curve_id for curve_id in normalized):
                raise LasExportError("Curve id cannot be empty.")
            if len(normalized) != len(set(normalized)):
                raise LasExportError(
                    "A curve can be selected only once for LAS."
                )
            try:
                candidate_definitions = tuple(
                    self.project.get_curve(curve_id)
                    for curve_id in normalized
                )
            except KeyError as exc:
                raise LasExportError(
                    f"Unknown project curve id: {exc.args[0]}"
                ) from exc

        assemblies: dict[str, AssembledProjectCurve] = {}
        definitions: list[CurveDefinition] = []
        for curve in candidate_definitions:
            assembly = self.get_project_curve_assembly(curve.curve_id)
            assemblies[curve.curve_id] = assembly
            if curve_ids is not None or assembly.sources:
                definitions.append(curve)

        selected_units = {
            assemblies[curve.curve_id].unit
            for curve in definitions
            if assemblies[curve.curve_id].unit is not None
        }
        resolution_unit = (
            next(iter(selected_units))
            if len(selected_units) == 1
            else None
        )
        return (
            assemblies,
            tuple(definitions),
            self.get_first_image_vertical_resolution(resolution_unit),
        )

    def export_las(
        self,
        path: Path,
        *,
        step_depth: float,
        curve_ids: Sequence[str] | None = None,
        metadata: LasWellMetadata | None = None,
        null_value: float = DEFAULT_LAS_NULL_VALUE,
    ) -> LasExportPlan:
        """Plan and atomically write one unwrapped LAS 2.0 file."""
        plan = self.prepare_las_export(
            step_depth=step_depth,
            curve_ids=curve_ids,
            null_value=null_value,
        )
        write_las_2(
            path,
            plan,
            self.get_las_metadata() if metadata is None else metadata,
        )
        return plan

    def sample_project_curves(
        self,
        *,
        start_depth: float,
        stop_depth: float,
        step_depth: float,
        curve_ids: Sequence[str] | None = None,
    ) -> SampledProjectCurves:
        """Evaluate selected curves on one regular, LAS-style depth grid.

        The returned matrix keeps missing samples as ``None``. Converting them
        to the LAS NULL sentinel belongs to the later file-format writer.
        """
        if self.project is None:
            raise RuntimeError("No project is open.")

        selected_ids = (
            tuple(curve.curve_id for curve in self.project.curves)
            if curve_ids is None
            else tuple(
                str(curve_id).strip() for curve_id in curve_ids
            )
        )
        if not selected_ids:
            raise ScientificSamplingError(
                "Select at least one project curve for sampling."
            )
        if any(not curve_id for curve_id in selected_ids):
            raise ScientificSamplingError("Curve id cannot be empty.")
        if len(selected_ids) != len(set(selected_ids)):
            raise ScientificSamplingError(
                "A curve can appear only once in the sampling request."
            )

        assemblies: dict[str, AssembledProjectCurve] = {}
        for curve_id in selected_ids:
            self.project.get_curve(curve_id)
            assemblies[curve_id] = self.get_project_curve_assembly(curve_id)

        return sample_assembled_project_curves(
            assemblies,
            DepthSamplingDefinition(
                start_depth=start_depth,
                stop_depth=stop_depth,
                step_depth=step_depth,
            ),
        )

    def set_digitized_curve(self, digitized: DigitizedCurve) -> None:
        """Persist one curve digitization in the active scanned segment."""
        segment = self._require_active_segment()
        if self.project is None:
            raise RuntimeError("No project is open.")
        self.project.get_curve(digitized.curve_id)
        segment.set_digitized_curve(digitized)
        self._refresh_digitization_status(segment)
        self.project.touch()
        self.dirty = True
        logger.info(
            "Updated digitization for segment %s curve %s (%d section(s))",
            segment.segment_id,
            digitized.curve_id,
            len(digitized.sections),
        )

    def remove_digitized_curve(self, curve_id: str) -> DigitizedCurve:
        """Remove one curve digitization from the active scanned segment."""
        segment = self._require_active_segment()
        if self.project is None:
            raise RuntimeError("No project is open.")
        self.project.get_curve(curve_id)
        removed = segment.remove_digitized_curve(curve_id)
        self._refresh_digitization_status(segment)
        self.project.touch()
        self.dirty = True
        logger.info(
            "Removed digitization for segment %s curve %s",
            segment.segment_id,
            curve_id,
        )
        return removed

    @staticmethod
    def _refresh_digitization_status(segment: Segment) -> None:
        """Derive a lightweight segment summary from persisted trace geometry."""
        has_geometry = any(
            section.control_points_px or section.raw_samples_px
            for digitized in segment.digitizations
            for section in digitized.sections
        )
        segment.digitization_status = (
            StageStatus.IN_PROGRESS
            if has_geometry
            else StageStatus.NOT_STARTED
        )

    def apply_rectification(
        self,
        rectified_image: np.ndarray,
        transform: PerspectiveTransformResult | MultipointTransformResult,
    ) -> Path:
        """Persist standard or multipoint rectification and reproducible geometry."""
        if (
            not self.project
            or not self.paths
            or not self.active_segment
        ):
            raise RuntimeError(
                "An active project segment is required."
            )

        segment = self.active_segment
        source_path = self.resolve_segment_source(
            segment
        )
        if source_path is None:
            raise RuntimeError(
                "The active segment has no resolvable source image."
            )

        derived_name = (
            f"{Path(segment.name).stem}_"
            f"{segment.segment_id[:8]}_rectified.png"
        )
        destination = (
            self.paths.preprocessed
            / derived_name
        ).resolve()

        if destination == source_path.resolve():
            raise RuntimeError(
                "A derived rectification may never overwrite the source image."
            )

        saved_path = save_derived_image(
            rectified_image,
            destination,
        )

        if isinstance(transform, MultipointTransformResult):
            base_transform = transform.base_transform
            preprocess_payload = {
                "mode": "multipoint_pairs",
                "corner_points": [
                    [point.x, point.y]
                    for point in base_transform.source_points
                ],
                "ordered_points": [
                    [point.x, point.y]
                    for point in base_transform.ordered_points
                ],
                "control_pairs": [
                    [
                        [pair.left.x, pair.left.y],
                        [pair.right.x, pair.right.y],
                    ]
                    for pair in transform.control_pairs
                ],
                "destination_y": list(transform.destination_y),
                "multipoint_interpolation": transform.interpolation,
                "transform_matrix": (
                    base_transform.transform_matrix.tolist()
                ),
                "inverse_matrix": (
                    base_transform.inverse_matrix.tolist()
                ),
                "output_size": [
                    transform.output_width,
                    transform.output_height,
                ],
                "reference_grid_size": [
                    transform.reference_size[0],
                    transform.reference_size[1],
                ],
                "canvas_offset": [
                    transform.canvas_offset_x,
                    transform.canvas_offset_y,
                ],
                "rectification_scope": (
                    "full_image"
                    if transform.full_image
                    else "reference_quad"
                ),
                "derived_path": to_project_relative(
                    saved_path,
                    self.paths.root,
                ),
            }
        else:
            preprocess_payload = {
                "mode": "perspective_4pt",
                "corner_points": [
                    [point.x, point.y]
                    for point in transform.source_points
                ],
                "ordered_points": [
                    [point.x, point.y]
                    for point in transform.ordered_points
                ],
                "transform_matrix": (
                    transform.transform_matrix.tolist()
                ),
                "inverse_matrix": (
                    transform.inverse_matrix.tolist()
                ),
                "output_size": [
                    transform.output_width,
                    transform.output_height,
                ],
                "reference_grid_size": [
                    transform.reference_size[0],
                    transform.reference_size[1],
                ],
                "canvas_offset": [
                    transform.canvas_offset_x,
                    transform.canvas_offset_y,
                ],
                "rectification_scope": (
                    "full_image"
                    if transform.full_image
                    else "reference_quad"
                ),
                "derived_path": to_project_relative(
                    saved_path,
                    self.paths.root,
                ),
            }

        segment.extensions["preprocess"] = (
            preprocess_payload
        )
        segment.preprocess_status = (
            StageStatus.COMPLETE
        )
        segment.mark_downstream_review_required(
            "preprocess"
        )

        self.project.touch()
        self.dirty = True

        logger.info(
            "Applied %s rectification to segment %s -> %s",
            (
                "multipoint"
                if preprocess_payload["mode"] == "multipoint_pairs"
                else "four-point"
            ),
            segment.segment_id,
            preprocess_payload["derived_path"],
        )

        return saved_path

    def apply_enhancement(
        self,
        enhanced_image: np.ndarray,
        settings: EnhancementSettings,
    ) -> Path:
        """Persist the final rectified + enhanced working raster and settings."""
        if (
            not self.project
            or not self.paths
            or not self.active_segment
        ):
            raise RuntimeError(
                "An active project segment is required."
            )

        validate_enhancement_settings(
            settings
        )

        segment = self.active_segment
        preprocess = segment.extensions.get(
            "preprocess",
            {},
        )

        derived_path = self.resolve_segment_preprocessed(
            segment
        )
        if (
            derived_path is None
            or not derived_path.is_file()
        ):
            raise RuntimeError(
                "Image enhancement requires an applied rectification first."
            )

        working_name = (
            f"{Path(segment.name).stem}_"
            f"{segment.segment_id[:8]}_working.png"
        )
        destination = (
            self.paths.preprocessed
            / working_name
        ).resolve()

        if destination == derived_path.resolve():
            raise RuntimeError(
                "The working image may never overwrite the rectified base image."
            )

        saved_path = save_derived_image(
            enhanced_image,
            destination,
        )

        preprocess["enhancement_settings"] = {
            "black_point": settings.black_point,
            "midtones": settings.midtones,
            "white_point": settings.white_point,
        }
        preprocess["working_path"] = (
            to_project_relative(
                saved_path,
                self.paths.root,
            )
        )

        segment.extensions["preprocess"] = (
            preprocess
        )
        segment.preprocess_status = (
            StageStatus.COMPLETE
        )

        # Image enhancement changes radiometry, not geometry. Therefore
        # calibration and manual digitization are deliberately not invalidated.
        self.project.touch()
        self.dirty = True

        logger.info(
            "Applied image enhancement to segment %s -> %s",
            segment.segment_id,
            preprocess["working_path"],
        )

        return saved_path
