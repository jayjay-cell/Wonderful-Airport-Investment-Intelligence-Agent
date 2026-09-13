"""US region -> state code mapping, for region-based airport discovery
(e.g. "New England airports"). Static reference data, not fetched live —
Census-style regional groupings are stable and don't need a data source.
"""

from __future__ import annotations

REGIONS: dict[str, list[str]] = {
    "new england": ["CT", "ME", "MA", "NH", "RI", "VT"],
    "mid-atlantic": ["NY", "NJ", "PA"],
    "midwest": ["IL", "IN", "MI", "OH", "WI", "IA", "KS", "MN", "MO", "NE", "ND", "SD"],
    "south": ["DE", "FL", "GA", "MD", "NC", "SC", "VA", "WV", "AL", "KY", "MS", "TN", "AR", "LA", "OK", "TX"],
    "southwest": ["AZ", "NM", "OK", "TX"],
    "west": ["AK", "CA", "CO", "HI", "ID", "MT", "NV", "OR", "UT", "WA", "WY"],
    "pacific": ["AK", "CA", "HI", "OR", "WA"],
}


def resolve_region(name: str) -> list[str] | None:
    """Returns the list of state codes for a named region, or None if the
    name isn't a recognized region (caller should then try treating it as a
    single state name/code instead)."""
    return REGIONS.get(name.strip().lower())


def known_region_names() -> list[str]:
    return list(REGIONS.keys())
