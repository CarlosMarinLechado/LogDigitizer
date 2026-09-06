from __future__ import annotations

from logdigitizer.domain.calibration import ScaleType
from logdigitizer.domain.curve_catalog import (
    OTHER_CURVE_KEY,
    curve_catalog_entries,
    curve_catalog_entry,
    curve_catalog_entry_for_parameter,
)


def test_catalog_contains_practical_core_curves_and_no_track_concept():
    entries = curve_catalog_entries()
    parameters = {entry.parameter for entry in entries}

    assert "Gamma Ray" in parameters
    assert "Electrical Resistivity" in parameters
    assert "Spontaneous Potential" in parameters
    assert "Bulk Density" in parameters
    assert "Magnetic Susceptibility" in parameters
    assert all(entry.key != OTHER_CURVE_KEY for entry in entries)


def test_resistivity_catalog_entry_suggests_common_choices():
    entry = curve_catalog_entry("resistivity")
    assert entry is not None
    assert "RES" in entry.mnemonics
    assert "ILD" in entry.mnemonics
    assert "OHMM" in entry.units
    assert entry.suggested_scale is ScaleType.LOGARITHMIC


def test_catalog_lookup_by_displayed_parameter():
    entry = curve_catalog_entry_for_parameter("gamma ray")
    assert entry is not None
    assert entry.key == "gamma_ray"
    assert "GR" in entry.mnemonics
