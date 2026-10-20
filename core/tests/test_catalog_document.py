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
