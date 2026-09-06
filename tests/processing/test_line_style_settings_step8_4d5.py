
from __future__ import annotations

import pytest

from logdigitizer.processing.automatic_digitization import AutomaticTraceSettings


def test_settings_schema_no_longer_contains_line_style() -> None:
    assert "line_style" not in AutomaticTraceSettings.__dataclass_fields__


def test_removed_line_style_argument_is_rejected() -> None:
    with pytest.raises(TypeError, match="line_style"):
        AutomaticTraceSettings(line_style="continuous")
