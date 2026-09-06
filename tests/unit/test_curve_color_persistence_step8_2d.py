from __future__ import annotations

import json
from pathlib import Path

from logdigitizer.application.project_service import ProjectService
from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.curve_display import CURVE_DISPLAY_PALETTE


def test_project_round_trip_preserves_curve_display_color(tmp_path: Path) -> None:
    service = ProjectService()
    service.new_project(
        name="BH-01",
        project_directory=tmp_path / "project",
    )
    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#FF8C00",
    )
    service.save()

    reopened = ProjectService()
    loaded = reopened.open_project(service.paths.project_file)
    restored = loaded.get_curve(curve.curve_id)

    assert restored.display_color == "#FF8C00"


def test_old_project_curve_without_color_remains_loadable(tmp_path: Path) -> None:
    service = ProjectService()
    service.new_project(
        name="BH-legacy",
        project_directory=tmp_path / "legacy_project",
    )
    curve = service.add_curve(
        name="Gamma Ray",
        mnemonic="GR",
        unit="API",
        scale_type=ScaleType.LINEAR,
        display_color="#00B8D9",
    )
    service.save()

    project_file = service.paths.project_file
    payload = json.loads(project_file.read_text(encoding="utf-8"))
    del payload["project"]["curves"][0]["display_color"]
    project_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    reopened = ProjectService()
    loaded = reopened.open_project(project_file)
    restored = loaded.get_curve(curve.curve_id)

    assert restored.display_color in {
        item.hex_color for item in CURVE_DISPLAY_PALETTE
    }
