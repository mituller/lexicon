from lexicon.vectorstore import build_scope_filter, drop_empty, to_float32


def test_drop_empty_removes_empty_list() -> None:
    cleaned = drop_empty({"term": "a", "synonyms": [], "domain": "general"})
    assert "synonyms" not in cleaned
    assert cleaned == {"term": "a", "domain": "general"}


def test_drop_empty_removes_empty_string_and_none() -> None:
    cleaned = drop_empty({"term": "a", "pos": "", "source": None})
    assert cleaned == {"term": "a"}


def test_drop_empty_keeps_non_empty_values() -> None:
    cleaned = drop_empty({"synonyms": ["x"], "count": 0, "flag": False})
    assert cleaned == {"synonyms": ["x"], "count": 0, "flag": False}


def test_to_float32_rounds_to_32_bit() -> None:
    result = to_float32([0.1, 1.0, -2.5])
    assert result[1] == 1.0
    assert result[2] == -2.5
    # 0.1 is not exactly representable in float32; confirm it was actually rounded.
    assert result[0] != 0.1
    assert abs(result[0] - 0.1) < 1e-7


def test_to_float32_returns_python_floats() -> None:
    assert all(isinstance(value, float) for value in to_float32([1, 2, 3]))


def test_scope_filter_global_only() -> None:
    assert build_scope_filter(None) == {"scope": {"$eq": "global"}}
    assert build_scope_filter("") == {"scope": {"$eq": "global"}}
