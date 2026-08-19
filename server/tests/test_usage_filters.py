import pytest
from fastapi import HTTPException

from metergraph_server.usage import _filters


def test_excluding_demo_keeps_untagged_and_other_environments():
    where, params = _filters(None, "demo", None, None)

    assert "(environment is null or environment <> %s)" in where
    assert params == ["demo"]


def test_exact_and_excluded_environment_are_mutually_exclusive():
    with pytest.raises(HTTPException) as exc:
        _filters("prod", "demo", None, None)

    assert exc.value.status_code == 400
