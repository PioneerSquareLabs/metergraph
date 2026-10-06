"""Counting a call's searches is catalog knowledge: every consumer that
prices a call reads the same fields the same way and bills them as the same
operation on each channel."""

import pytest

from metergraph_core import count_search_units, load_catalog, search_operation_for_channel
from metergraph_core.retrieval import SEARCH_OPERATION_BY_CHANNEL


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"web_search_calls": 3}, 3),
        ({"grounding_queries": 2, "tool_calls": [{"type": "web_search_call"}]}, 2),
        ({"server_tool_use": {"web_search_requests": 4}}, 4),
        ({"tool_calls": [{"type": "web_search_call"}, {"type": "function", "name": "lookup"}, {"name": "web_search"}]}, 2),
        ({"provider": "perplexity", "model": "sonar"}, 1),
        ({"provider": "Perplexity-AI"}, 1),
        ({"provider": "openai", "tool_calls": [{"type": "function"}]}, None),
        ({"web_search_calls": "many"}, None),
        ({"web_search_calls": True}, None),
        ({}, None),
    ],
)
def test_searches_are_read_from_the_first_field_that_carries_them(row, expected):
    assert count_search_units(row) == expected


def test_each_fee_charging_channel_names_its_operation():
    assert search_operation_for_channel("openai-api") == "web_search"
    assert search_operation_for_channel("google-vertex-ai") == "google_search_grounding"
    assert search_operation_for_channel("perplexity-api") == "search_request_low"
    assert search_operation_for_channel("deepseek-api") is None
    assert search_operation_for_channel(None) is None


def test_every_named_operation_is_priced_in_the_shipped_catalog():
    catalog = load_catalog()
    for channel, operation in SEARCH_OPERATION_BY_CHANNEL.items():
        result = catalog.price_retrieval(channel=channel, operation=operation, units=1, at="2026-10-06")
        assert result.status == "priced", (channel, operation, result)
