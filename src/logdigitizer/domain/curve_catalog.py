"""Small practical starter catalog for curve-definition assistance.

The catalog is intentionally a UI aid, not a normative restriction. It is a
curated subset inspired by the property/mnemonic normalization goals of the
Energistics Practical Well Log Standard (PWLS), with names and units chosen for
common historical well-log digitization workflows. Users can always select
"Other" and enter arbitrary names, mnemonics and units.
"""

from __future__ import annotations

from dataclasses import dataclass

from .calibration import ScaleType


OTHER_CURVE_KEY = "other"


@dataclass(frozen=True, slots=True)
class CurveCatalogEntry:
    """One assisted curve-definition option."""

    key: str
    parameter: str
    mnemonics: tuple[str, ...]
    units: tuple[str, ...]
    suggested_scale: ScaleType
    description: str


CURVE_CATALOG: tuple[CurveCatalogEntry, ...] = (
    CurveCatalogEntry(
        "gamma_ray",
        "Gamma Ray",
        ("GR", "SGR", "CGR"),
        ("API",),
        ScaleType.LINEAR,
        "Natural gamma-ray response.",
    ),
    CurveCatalogEntry(
        "spontaneous_potential",
        "Spontaneous Potential",
        ("SP",),
        ("MV",),
        ScaleType.LINEAR,
        "Spontaneous-potential / self-potential curve.",
    ),
    CurveCatalogEntry(
        "resistivity",
        "Electrical Resistivity",
        ("RES", "ILD", "ILM", "LLD", "LLS", "RT"),
        ("OHMM",),
        ScaleType.LOGARITHMIC,
        "Formation or apparent electrical resistivity.",
    ),
    CurveCatalogEntry(
        "conductivity",
        "Electrical Conductivity",
        ("COND",),
        ("MS/M", "S/M"),
        ScaleType.LINEAR,
        "Electrical conductivity.",
    ),
    CurveCatalogEntry(
        "sonic_slowness",
        "Sonic / Acoustic Slowness",
        ("DT", "DTC", "AC"),
        ("US/F", "US/M"),
        ScaleType.LINEAR,
        "Acoustic interval transit time / slowness.",
    ),
    CurveCatalogEntry(
        "compressional_velocity",
        "P-wave Velocity",
        ("VP",),
        ("M/S", "FT/S"),
        ScaleType.LINEAR,
        "Compressional-wave velocity.",
    ),
    CurveCatalogEntry(
        "bulk_density",
        "Bulk Density",
        ("RHOB", "DEN"),
        ("G/C3", "KG/M3"),
        ScaleType.LINEAR,
        "Bulk density.",
    ),
    CurveCatalogEntry(
        "neutron_porosity",
        "Neutron Porosity",
        ("NPHI", "TNPH", "CNL"),
        ("V/V", "%", "PU"),
        ScaleType.LINEAR,
        "Neutron-derived porosity.",
    ),
    CurveCatalogEntry(
        "caliper",
        "Caliper / Borehole Diameter",
        ("CALI", "CAL"),
        ("IN", "MM", "CM"),
        ScaleType.LINEAR,
        "Borehole diameter / caliper response.",
    ),
    CurveCatalogEntry(
        "temperature",
        "Temperature",
        ("TEMP",),
        ("DEGC", "DEGF"),
        ScaleType.LINEAR,
        "Borehole or fluid temperature.",
    ),
    CurveCatalogEntry(
        "magnetic_susceptibility",
        "Magnetic Susceptibility",
        ("MSUS", "SUSC"),
        ("SI", "10^-5 SI"),
        ScaleType.LINEAR,
        "Magnetic susceptibility.",
    ),
    CurveCatalogEntry(
        "fluid_resistivity",
        "Borehole Fluid Resistivity",
        ("RFL", "RM"),
        ("OHMM",),
        ScaleType.LINEAR,
        "Electrical resistivity of borehole fluid or mud.",
    ),
)


def curve_catalog_entries() -> tuple[CurveCatalogEntry, ...]:
    """Return the immutable assisted catalog."""
    return CURVE_CATALOG


def curve_catalog_entry(key: str) -> CurveCatalogEntry | None:
    """Find one catalog entry by stable key."""
    normalized = key.strip().lower()
    return next(
        (entry for entry in CURVE_CATALOG if entry.key == normalized),
        None,
    )


def curve_catalog_entry_for_parameter(
    parameter: str,
) -> CurveCatalogEntry | None:
    """Find one catalog entry by its displayed parameter name."""
    normalized = parameter.strip().casefold()
    return next(
        (
            entry
            for entry in CURVE_CATALOG
            if entry.parameter.casefold() == normalized
        ),
        None,
    )
