from core.conflicts import NOT_FOUND, ConflictCheckInput, as_value_or_not_found, resolve_conflict


def _candidate(value, source_type="faa_government", is_current=True, **overrides):
    defaults = dict(
        field="passengers",
        definition="enplaned_passengers",
        period="2024",
        unit="count",
        scope="domestic",
        value=value,
        source_type=source_type,
        is_current=is_current,
    )
    defaults.update(overrides)
    return ConflictCheckInput(**defaults)


def test_single_source_resolves_trivially():
    result = resolve_conflict([_candidate(100)])
    assert result.status == "resolved"
    assert result.resolved_value == 100


def test_agreeing_sources_resolve():
    result = resolve_conflict([_candidate(100), _candidate(100, source_type="bts_government")])
    assert result.status == "resolved"
    assert result.resolved_value == 100


def test_different_definitions_are_different_metrics_not_conflict():
    a = _candidate(100, definition="enplaned_passengers")
    b = _candidate(200, definition="total_passengers")  # different definition
    result = resolve_conflict([a, b])
    assert result.status == "different_metrics"
    assert result.resolved_value is None


def test_disagreeing_sources_prefer_more_authoritative():
    a = _candidate(100, source_type="news")
    b = _candidate(200, source_type="faa_government")
    result = resolve_conflict([a, b])
    assert result.status == "resolved"
    assert result.resolved_value == 200  # government beats news


def test_disagreeing_sources_of_equal_authority_are_unresolved_conflict():
    a = _candidate(100, source_type="faa_government")
    b = _candidate(200, source_type="faa_government")
    result = resolve_conflict([a, b])
    assert result.status == "data_conflict"
    assert result.resolved_value is None  # never averaged


def test_conflict_never_averages_values():
    a = _candidate(100, source_type="faa_government")
    b = _candidate(300, source_type="faa_government")
    result = resolve_conflict([a, b])
    assert result.resolved_value != 200  # would be the average — must not happen
    assert result.resolved_value is None


def test_not_found_is_not_zero():
    assert as_value_or_not_found(None) is NOT_FOUND
    assert as_value_or_not_found(0) == 0  # a real zero is preserved, not conflated
    assert as_value_or_not_found(0) is not NOT_FOUND
