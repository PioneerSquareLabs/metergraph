"""Building a catalog from an already parsed document, and how that path
relates to loading the same catalog from a file."""

import copy
import hashlib
import json
from datetime import date, datetime, timezone

import pytest
import yaml

from metergraph_core import CatalogError, ModelUsage, load_catalog, load_catalog_document
from metergraph_core import loader as loader_module
from metergraph_core.loader import DEFAULT_CATALOG_PATH

AT = datetime(2026, 8, 17, tzinfo=timezone.utc)


def _price_ids(loaded):
    # Neither snapshot exposes its rows publicly; their ids are the identity
    # the equivalence check needs.
    token = {p.id for prices in loaded.snapshot._prices.values() for p in prices}
    retrieval = {p.id for prices in loaded.retrieval._by_key.values() for p in prices}
    return sorted(token), sorted(retrieval)


def _small_document():
    return {
        "version": "test",
        "currency": "USD",
        "pricing_verified_at": "2026-10-01",
        "models": [
            {
                "canonical_id": "example/model-a",
                "publisher": "example",
                "aliases": [
                    {"provider": "openai", "alias": "model-a", "channel": "openai-api"}
                ],
                "prices": [
                    {
                        "channel": "openai-api",
                        "region": "global",
                        "effective_from": "2026-01-01",
                        "input_per_mtok": 1.0,
                        "output_per_mtok": 4.0,
                        "source_url": "https://example.com/pricing",
                    }
                ],
            }
        ],
    }


def test_document_load_matches_file_load_except_content_hash():
    from_file = load_catalog()
    from_document = load_catalog_document(
        yaml.safe_load(DEFAULT_CATALOG_PATH.read_bytes())
    )

    assert from_document.version == from_file.version
    assert from_document.currency == from_file.currency
    assert from_document.pricing_verified_at == from_file.pricing_verified_at
    assert from_document.document == from_file.document
    assert dict(from_document.canonical_ids) == dict(from_file.canonical_ids)
    assert from_document.disputes == from_file.disputes
    assert _price_ids(from_document) == _price_ids(from_file)
    token_ids, retrieval_ids = _price_ids(from_file)
    assert len(token_ids) > 300 and retrieval_ids
    usage = ModelUsage(input_tokens=100_000, output_tokens=100_000)
    for loaded in (from_file, from_document):
        result = loaded.calculate_cost(
            model="gpt-5.4-mini", provider="openai", at=AT, usage=usage
        )
        assert result.price.id == "openai/gpt-5.4-mini:openai-api:global:2026-03-17"
        assert str(result.cost) == "0.52500000"


def test_document_load_honors_region():
    document = _small_document()
    document["models"][0]["prices"].append(
        {
            "channel": "openai-api",
            "region": "us",
            "effective_from": "2026-01-01",
            "input_per_mtok": 1.1,
            "output_per_mtok": 4.4,
            "source_url": "https://example.com/pricing",
        }
    )
    loaded = load_catalog_document(document, region="us")
    found = loaded.lookup_price(model="model-a", provider="openai", at=AT)
    assert str(found.price.input_per_mtok) == "1.1"


def test_file_load_hash_is_raw_byte_sha256():
    raw = DEFAULT_CATALOG_PATH.read_bytes()
    assert load_catalog().content_hash == hashlib.sha256(raw).hexdigest()


def test_document_load_hash_is_sha256_of_sorted_compact_json():
    document = _small_document()
    expected = hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert load_catalog_document(document).content_hash == expected


def test_document_hash_ignores_key_order():
    document = _small_document()
    reordered = dict(reversed(list(document.items())))
    reordered["models"] = [dict(reversed(list(document["models"][0].items())))]
    assert (
        load_catalog_document(reordered).content_hash
        == load_catalog_document(document).content_hash
    )


def test_document_hash_normalizes_dates_to_iso_strings():
    quoted = _small_document()
    unquoted = _small_document()
    unquoted["pricing_verified_at"] = date(2026, 10, 1)
    unquoted["models"][0]["prices"][0]["effective_from"] = date(2026, 1, 1)
    assert (
        load_catalog_document(unquoted).content_hash
        == load_catalog_document(quoted).content_hash
    )


def test_document_hash_changes_with_a_price():
    document = _small_document()
    changed = _small_document()
    changed["models"][0]["prices"][0]["input_per_mtok"] = 1.5
    assert (
        load_catalog_document(changed).content_hash
        != load_catalog_document(document).content_hash
    )


def test_document_load_deep_copies_input():
    document = _small_document()
    loaded = load_catalog_document(document)
    document["models"][0]["prices"][0]["input_per_mtok"] = 99.0
    document["version"] = "changed"
    assert loaded.document["models"][0]["prices"][0]["input_per_mtok"] == 1.0
    assert loaded.document["version"] == "test"


def test_document_load_does_not_mutate_input():
    document = _small_document()
    before = copy.deepcopy(document)
    load_catalog_document(document)
    assert document == before


def _without(path):
    document = _small_document()
    target = document
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    return document


def _with(path, value):
    document = _small_document()
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return document


def _overlapping():
    document = _small_document()
    document["models"][0]["prices"].append(
        dict(document["models"][0]["prices"][0], effective_from="2026-02-01")
    )
    return document


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (["not", "a", "mapping"], "must have a models list"),
        (_without(["models"]), "must have a models list"),
        (_without(["version"]), "must have a version"),
        (_with(["currency"], "EUR"), "currency must be USD"),
        (_with(["pricing_verified_at"], "yesterday"), "pricing_verified_at"),
        (_without(["models", 0, "prices", 0, "source_url"]), "needs source_url"),
        (_overlapping(), "overlapping openai-api/global price windows"),
        (_with(["retrieval"], {"not": "a list"}), "retrieval must be a list"),
        (_with(["disputes"], [{"gateway": "x"}]), "needs gateway/publisher"),
    ],
)
def test_document_load_rejects_invalid_documents_like_the_file_loader(
    tmp_path, document, message
):
    path = tmp_path / "prices.yaml"
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(CatalogError, match=message) as from_file:
        load_catalog(path)
    with pytest.raises(CatalogError, match=message) as from_document:
        load_catalog_document(document)
    assert str(from_document.value) == str(from_file.value)


# --- Price ids name rows by UTC date --------------------------------------------


def _price_id_document(*starts):
    document = _small_document()
    row = document["models"][0]["prices"][0]
    rows = []
    for index, effective_from in enumerate(starts):
        later = starts[index + 1] if index + 1 < len(starts) else None
        rows.append(dict(row, effective_from=effective_from, **({"effective_to": later} if later else {})))
    document["models"][0]["prices"] = rows
    return document


@pytest.mark.parametrize(
    ("effective_from", "id_date"),
    [
        ("2025-01-02", "2025-01-02"),
        ("2025-01-02T00:00:00Z", "2025-01-02"),
        ("2025-01-02T00:00:00+00:00", "2025-01-02"),
        ("2025-01-02T23:30:00-05:00", "2025-01-03"),
        ("2025-01-02T00:00:00+14:00", "2025-01-01"),
    ],
)
def test_a_price_id_uses_the_utc_date_of_its_start(effective_from, id_date):
    [ids] = [_price_ids(load_catalog_document(_price_id_document(effective_from)))[0]]
    assert ids == [f"example/model-a:openai-api:global:{id_date}"]


def test_offset_starts_on_one_local_date_get_distinct_ids():
    # Both start on 2 January in their own offsets, but on different UTC
    # dates: 10:00 UTC on 1 January, then midnight UTC on 2 January.
    loaded = load_catalog_document(_price_id_document("2025-01-02T00:00:00+14:00", "2025-01-02T00:00:00Z"))
    assert _price_ids(loaded)[0] == [
        "example/model-a:openai-api:global:2025-01-01",
        "example/model-a:openai-api:global:2025-01-02",
    ]


def test_a_retrieval_price_id_uses_the_utc_date_of_its_start():
    document = _small_document()
    document["retrieval"] = [
        {
            "channel": "anthropic-api",
            "operation": "web_search",
            "unit": "completed_search",
            "per_1k_usd": "10",
            "effective_from": "2025-01-02T00:00:00+14:00",
            "source_url": "https://example.com/search",
        }
    ]
    assert _price_ids(load_catalog_document(document))[1] == ["anthropic-api:web_search:global:2025-01-01"]


def test_rows_starting_on_one_utc_date_keep_distinct_ids():
    # 23:00 at -05:00 on 1 January is 04:00 UTC on 2 January; the next row
    # starts at 10:00 UTC that day. The windows do not overlap.
    loaded = load_catalog_document(_price_id_document("2025-01-01T23:00:00-05:00", "2025-01-02T10:00:00Z"))
    assert _price_ids(loaded)[0] == [
        "example/model-a:openai-api:global:2025-01-02T04:00:00Z",
        "example/model-a:openai-api:global:2025-01-02T10:00:00Z",
    ]


def test_a_utc_midnight_row_keeps_its_date_id_beside_a_same_day_row():
    loaded = load_catalog_document(_price_id_document("2025-01-02", "2025-01-02T06:30:00Z"))
    assert _price_ids(loaded)[0] == [
        "example/model-a:openai-api:global:2025-01-02",
        "example/model-a:openai-api:global:2025-01-02T06:30:00Z",
    ]


def test_rows_starting_within_one_second_keep_distinct_ids():
    loaded = load_catalog_document(_price_id_document("2025-01-02T10:00:00Z", "2025-01-02T10:00:00.5Z"))
    assert _price_ids(loaded)[0] == [
        "example/model-a:openai-api:global:2025-01-02T10:00:00.500000Z",
        "example/model-a:openai-api:global:2025-01-02T10:00:00Z",
    ]


def test_same_utc_date_rows_on_other_channels_keep_date_ids():
    document = _price_id_document("2025-01-02T10:00:00Z")
    document["models"][0]["prices"].append(
        dict(document["models"][0]["prices"][0], channel="vercel-ai-gateway", effective_from="2025-01-02T12:00:00Z")
    )
    assert _price_ids(load_catalog_document(document))[0] == [
        "example/model-a:openai-api:global:2025-01-02",
        "example/model-a:vercel-ai-gateway:global:2025-01-02",
    ]


def test_retrieval_rows_starting_on_one_utc_date_keep_distinct_ids():
    document = _small_document()
    base = {
        "channel": "anthropic-api",
        "operation": "web_search",
        "unit": "completed_search",
        "per_1k_usd": "10",
        "source_url": "https://example.com/search",
    }
    document["retrieval"] = [
        dict(base, effective_from="2025-01-01T23:00:00-05:00", effective_to="2025-01-02T10:00:00Z"),
        dict(base, effective_from="2025-01-02T10:00:00Z"),
    ]
    assert _price_ids(load_catalog_document(document))[1] == [
        "anthropic-api:web_search:global:2025-01-02T04:00:00Z",
        "anthropic-api:web_search:global:2025-01-02T10:00:00Z",
    ]


def test_bundled_price_ids_keep_their_published_dates():
    # Every bundled start is a date or a UTC datetime, so each id's date is
    # the first ten characters of its effective_from as written.
    document = yaml.safe_load(DEFAULT_CATALOG_PATH.read_bytes())
    expected = sorted(
        f"{entry['canonical_id']}:{row['channel']}:{row.get('region') or 'global'}:{str(row['effective_from'])[:10]}"
        for entry in document["models"]
        for row in entry.get("prices") or []
    )
    retrieval = sorted(
        f"{row['channel']}:{row['operation']}:{row.get('region') or 'global'}:{str(row['effective_from'])[:10]}"
        for row in document.get("retrieval") or []
    )
    assert _price_ids(load_catalog()) == (expected, retrieval)


# --- Dates ending in Z, and one entry per model ------------------------------------


class _Python310Datetime(datetime):
    """datetime whose fromisoformat, like Python 3.10's, rejects a Z suffix."""

    @classmethod
    def fromisoformat(cls, text):
        if text[-1:] in ("Z", "z"):
            raise ValueError(f"Invalid isoformat string: {text!r}")
        return super().fromisoformat(text)


@pytest.mark.parametrize("suffix", ["Z", "z"])
def test_a_utc_z_suffix_parses_on_every_supported_python(monkeypatch, suffix):
    monkeypatch.setattr(loader_module, "datetime", _Python310Datetime)
    document = _price_id_document(f"2025-01-02T10:00:00{suffix}")
    document["models"][0]["aliases"][0].update(
        effective_from=f"2025-01-01T00:00:00{suffix}", source_url="https://example.com/aliases"
    )
    loaded = load_catalog_document(document)
    assert _price_ids(loaded)[0] == ["example/model-a:openai-api:global:2025-01-02"]
    found = loaded.lookup_price(model="model-a", provider="openai", at=datetime(2025, 1, 2, 10, tzinfo=timezone.utc))
    assert found.price.effective_from == datetime(2025, 1, 2, 10, tzinfo=timezone.utc)


def test_duplicate_model_entries_are_rejected():
    document = _small_document()
    duplicate = copy.deepcopy(document["models"][0])
    duplicate["aliases"] = []
    duplicate["prices"][0]["channel"] = "vercel-ai-gateway"
    document["models"].append(duplicate)
    with pytest.raises(CatalogError, match="duplicate model entry 'example/model-a'"):
        load_catalog_document(document)


def test_a_lookup_time_with_a_z_suffix_parses_on_every_supported_python(monkeypatch):
    from metergraph_core import catalog as catalog_module

    loaded = load_catalog_document(_small_document())
    monkeypatch.setattr(catalog_module, "datetime", _Python310Datetime)
    found = loaded.lookup_price(model="model-a", provider="openai", at="2026-10-20T10:00:00Z")
    assert found.status == "priced"
    assert found.effective_at == datetime(2026, 10, 20, 10, tzinfo=timezone.utc)


def test_a_registry_window_day_with_a_z_suffix_parses_on_every_supported_python(monkeypatch):
    from metergraph_core import models as models_module

    monkeypatch.setattr(models_module, "datetime", _Python310Datetime)
    assert models_module._window_day("2026-09-10T04:00:00Z") == date(2026, 9, 10)


def test_both_loaders_build_through_one_construction_path(tmp_path, monkeypatch):
    calls = []
    real = loader_module._build_loaded_catalog

    def spy(document, *, content_hash, region):
        calls.append((document["version"], content_hash, region))
        return real(document, content_hash=content_hash, region=region)

    monkeypatch.setattr(loader_module, "_build_loaded_catalog", spy)
    path = tmp_path / "prices.yaml"
    path.write_text(yaml.safe_dump(_small_document()))

    from_file = load_catalog(path, region="us")
    from_document = load_catalog_document(_small_document(), region="eu")

    assert calls == [
        ("test", from_file.content_hash, "us"),
        ("test", from_document.content_hash, "eu"),
    ]
