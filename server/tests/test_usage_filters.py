from metergraph_server.usage import _filters


def test_selecting_multiple_environments_includes_each_name():
    where, params = _filters(["demo", "prod"], False, None, None)

    assert "environment = any(%s)" in where
    assert params == [["demo", "prod"]]


def test_selecting_named_and_untagged_environments_keeps_null_rows():
    where, params = _filters(["prod"], True, None, None)

    assert "(environment = any(%s) or environment is null)" in where
    assert params == [["prod"]]


def test_selecting_only_untagged_environment_keeps_only_null_rows():
    where, params = _filters([], True, None, None)

    assert "environment is null" in where
    assert params == []


def test_selecting_no_environments_returns_no_rows():
    where, params = _filters([], False, None, None)

    assert "false" in where
    assert params == []


def test_omitting_environment_selection_keeps_all_rows():
    where, params = _filters(None, None, None, None)

    assert where == ""
    assert params == []
