"""The public pricing source registry: its schema, its seeded entries, and
their fixtures."""

from decimal import Decimal

import pytest

from metergraph_core import load_catalog_document
from metergraph_core.sources import (
    SOURCE_REGISTRY_SCHEMA_VERSION,
    SourceRegistryError,
    parse_source_registry,
    validate_source_registry,
)


def _entry(**overrides):
    entry = {
        "id": "example-api-pricing",
        "provider": "openai",
        "channel": "openai-api",
        "catalog_channel": True,
        "url": "https://example.com/pricing",
        "parser": "openai",
        "source_shape": "token_table",
        "clarity": "structured",
        "dimensions": ["input_per_mtok", "output_per_mtok", "cache_read_per_mtok"],
        "fingerprint": "sha256:" + "a" * 64,
        "low_risk": {
            "eligible": True,
            "max_relative_change": "0.5",
            "dimensions": ["input_per_mtok", "output_per_mtok"],
        },
        "evidence": {
            "store_raw": False,
            "redistribute_raw": False,
            "excerpt_chars": 0,
            "reason": "Provider terms do not grant redistribution of page content.",
        },
    }
    entry.update(overrides)
    return entry


def _registry(*entries, **overrides):
    document = {
        "schema_version": SOURCE_REGISTRY_SCHEMA_VERSION,
        "sources": list(entries) if entries else [_entry()],
    }
    document.update(overrides)
    return document


def _not_low_risk(**overrides):
    return _entry(
        low_risk={"eligible": False, "max_relative_change": None, "dimensions": []},
        **overrides,
    )


def test_parse_reads_an_entry():
    registry = parse_source_registry(_registry())
    source = registry.get("example-api-pricing")
    assert source.provider == "openai"
    assert source.channel == "openai-api"
    assert source.url == "https://example.com/pricing"
    assert source.parser == "openai"
    assert source.dimensions == ("input_per_mtok", "output_per_mtok", "cache_read_per_mtok")
    assert source.low_risk.eligible is True
    assert source.low_risk.max_relative_change == Decimal("0.5")
    assert source.low_risk.dimensions == ("input_per_mtok", "output_per_mtok")
    assert source.evidence.store_raw is False
    assert source.evidence.reason.startswith("Provider terms")


def test_get_rejects_an_unknown_source():
    with pytest.raises(SourceRegistryError, match="unknown source 'missing'"):
        parse_source_registry(_registry()).get("missing")


def test_low_risk_lists_only_eligible_sources():
    registry = parse_source_registry(
        _registry(_entry(), _not_low_risk(id="other-pricing", url="https://example.com/other"))
    )
    assert [source.id for source in registry.low_risk()] == ["example-api-pricing"]


def test_allowed_urls_is_exactly_the_registered_urls():
    registry = parse_source_registry(
        _registry(
            _entry(),
            _not_low_risk(id="other-pricing", url="https://example.com/other"),
        )
    )
    assert registry.allowed_urls() == frozenset(
        {"https://example.com/pricing", "https://example.com/other"}
    )


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (["not", "a", "mapping"], "source registry must be a mapping"),
        (_registry(schema_version=2), "unsupported source registry schema_version 2"),
        (_registry(sources=[]), "source registry needs a non-empty sources list"),
        (_registry(_entry(), _entry()), "duplicate source id 'example-api-pricing'"),
        (_registry(_entry(id="Bad Id")), "invalid source id 'Bad Id'"),
        (_registry(_entry(provider="")), "example-api-pricing: needs provider"),
        (_registry(_entry(channel=None)), "example-api-pricing: needs channel"),
        (_registry(_entry(catalog_channel="yes")), "catalog_channel must be true or false"),
        (_registry(_entry(url="http://example.com/pricing")), "url must be an https URL"),
        (_registry(_entry(url="https://")), "url must be an https URL"),
        (_registry(_entry(parser="scraper")), "unknown parser 'scraper'"),
        (_registry(_entry(source_shape="pdf")), "unknown source_shape 'pdf'"),
        (_registry(_entry(clarity="clear")), "unknown clarity 'clear'"),
        (_registry(_entry(dimensions=["input_per_mtok", "vibes"])), "unknown dimension 'vibes'"),
        (_registry(_entry(dimensions=["input_per_mtok", "input_per_mtok"])), "duplicate dimension"),
        (_registry(_entry(fingerprint="abc")), "fingerprint must be null or sha256:"),
    ],
)
def test_parse_rejects_malformed_registries(document, message):
    with pytest.raises(SourceRegistryError, match=message):
        parse_source_registry(document)


def test_a_missing_fingerprint_is_allowed():
    assert parse_source_registry(_registry(_entry(fingerprint=None))).get(
        "example-api-pricing"
    ).fingerprint is None


def test_low_risk_requires_a_catalog_channel():
    with pytest.raises(SourceRegistryError, match="low-risk source needs a catalog channel"):
        parse_source_registry(_registry(_entry(catalog_channel=False)))


def test_low_risk_requires_a_parser_that_emits_price_rows():
    with pytest.raises(SourceRegistryError, match="parser 'fireworks' emits no price rows"):
        parse_source_registry(_registry(_entry(parser="fireworks")))


def test_low_risk_dimensions_must_be_dimensions_the_source_represents():
    low_risk = {
        "eligible": True,
        "max_relative_change": "0.5",
        "dimensions": ["input_per_mtok", "batch_input_per_mtok"],
    }
    with pytest.raises(SourceRegistryError, match="low-risk dimension 'batch_input_per_mtok' is not a source dimension"):
        parse_source_registry(_registry(_entry(low_risk=low_risk)))


def test_low_risk_needs_at_least_one_dimension():
    low_risk = {"eligible": True, "max_relative_change": "0.5", "dimensions": []}
    with pytest.raises(SourceRegistryError, match="low-risk source needs dimensions"):
        parse_source_registry(_registry(_entry(low_risk=low_risk)))


@pytest.mark.parametrize("bound", ["0", "1.5", "-0.1", 0.5, None, "half"])
def test_low_risk_bound_is_a_decimal_string_above_zero_up_to_one(bound):
    low_risk = {"eligible": True, "max_relative_change": bound, "dimensions": ["input_per_mtok"]}
    with pytest.raises(SourceRegistryError, match="max_relative_change must be a decimal string in"):
        parse_source_registry(_registry(_entry(low_risk=low_risk)))


def test_a_source_that_is_not_low_risk_carries_no_bounds():
    low_risk = {"eligible": False, "max_relative_change": "0.5", "dimensions": []}
    with pytest.raises(SourceRegistryError, match="non-low-risk source carries no low-risk bounds"):
        parse_source_registry(_registry(_entry(low_risk=low_risk)))
    low_risk = {"eligible": False, "max_relative_change": None, "dimensions": ["input_per_mtok"]}
    with pytest.raises(SourceRegistryError, match="non-low-risk source carries no low-risk bounds"):
        parse_source_registry(_registry(_entry(low_risk=low_risk)))


def test_low_risk_eligibility_must_be_a_boolean():
    with pytest.raises(SourceRegistryError, match="low_risk.eligible must be true or false"):
        parse_source_registry(_registry(_entry(low_risk={"eligible": "yes"})))


def test_gpu_or_license_sources_carry_no_token_dimensions():
    entry = _not_low_risk(
        parser="nvidia",
        source_shape="gpu_hour_or_license",
        dimensions=["input_per_mtok"],
    )
    with pytest.raises(SourceRegistryError, match="gpu_hour_or_license source cannot represent token dimension 'input_per_mtok'"):
        parse_source_registry(_registry(entry))


def test_token_sources_carry_no_gpu_or_license_dimensions():
    entry = _not_low_risk(dimensions=["gpu_hour_usd"])
    with pytest.raises(SourceRegistryError, match="token source cannot represent dimension 'gpu_hour_usd'"):
        parse_source_registry(_registry(entry))


def test_a_gpu_or_license_source_records_its_non_token_dimensions():
    entry = _not_low_risk(
        parser="nvidia",
        source_shape="gpu_hour_or_license",
        clarity="option_dependent",
        dimensions=["gpu_hour_usd", "license_per_gpu_year_usd"],
    )
    source = parse_source_registry(_registry(entry)).get("example-api-pricing")
    assert source.dimensions == ("gpu_hour_usd", "license_per_gpu_year_usd")


def test_one_provider_may_register_one_url_for_several_channels():
    bedrock = _not_low_risk(
        id="aws-bedrock-pricing",
        provider="aws",
        channel="aws-bedrock",
        parser="aws-bedrock",
        source_shape="token_option_dependent",
        url="https://example.com/bedrock",
    )
    geo = dict(bedrock, id="aws-bedrock-geo-pricing", channel="aws-bedrock-geo")
    registry = parse_source_registry(_registry(bedrock, geo))
    assert registry.allowed_urls() == frozenset({"https://example.com/bedrock"})


def test_two_providers_may_not_share_a_url():
    other = _not_low_risk(id="other-pricing", provider="anthropic", channel="anthropic-api")
    with pytest.raises(SourceRegistryError, match="url https://example.com/pricing is registered by providers anthropic and openai"):
        parse_source_registry(_registry(_entry(), other))


@pytest.mark.parametrize(
    ("evidence", "message"),
    [
        (None, "evidence must be a mapping"),
        ({"store_raw": "no", "redistribute_raw": False, "excerpt_chars": 0, "reason": "x"}, "evidence.store_raw must be true or false"),
        ({"store_raw": False, "redistribute_raw": True, "excerpt_chars": 0, "reason": "x"}, "raw content cannot be redistributed unless it may be stored"),
        ({"store_raw": False, "redistribute_raw": False, "excerpt_chars": -1, "reason": "x"}, "evidence.excerpt_chars must be a non-negative integer"),
        ({"store_raw": False, "redistribute_raw": False, "excerpt_chars": 0, "reason": " "}, "evidence needs a reason when raw content may not be stored or redistributed"),
    ],
)
def test_evidence_retention_records_what_may_be_kept_and_why(evidence, message):
    with pytest.raises(SourceRegistryError, match=message):
        parse_source_registry(_registry(_entry(evidence=evidence)))


def test_evidence_that_may_be_redistributed_needs_no_reason():
    evidence = {"store_raw": True, "redistribute_raw": True, "excerpt_chars": 200}
    source = parse_source_registry(_registry(_entry(evidence=evidence))).get(
        "example-api-pricing"
    )
    assert source.evidence.redistribute_raw is True
    assert source.evidence.reason is None


def _catalog():
    return load_catalog_document(
        {
            "version": "test",
            "currency": "USD",
            "pricing_verified_at": "2026-10-01",
            "models": [
                {
                    "canonical_id": "example/model-a",
                    "aliases": [{"provider": "openai", "alias": "model-a", "channel": "openai-api"}],
                    "prices": [
                        {
                            "channel": "openai-api",
                            "effective_from": "2026-01-01",
                            "input_per_mtok": 1,
                            "output_per_mtok": 2,
                            "source_url": "https://example.com/pricing",
                        }
                    ],
                }
            ],
        }
    )


def test_validate_accepts_catalog_and_registry_only_channels():
    azure = _not_low_risk(
        id="azure-foundry-pricing",
        provider="azure",
        channel="azure-foundry",
        catalog_channel=False,
        parser="azure-foundry",
        source_shape="token_option_dependent",
        url="https://example.com/azure",
    )
    validate_source_registry(parse_source_registry(_registry(_entry(), azure)), _catalog())


def test_validate_rejects_a_catalog_channel_the_catalog_lacks():
    entry = _not_low_risk(channel="fireworks-api", parser="fireworks")
    with pytest.raises(SourceRegistryError, match="example-api-pricing: catalog channel 'fireworks-api' has no catalog rows"):
        validate_source_registry(parse_source_registry(_registry(entry)), _catalog())


def test_validate_rejects_a_registry_only_channel_the_catalog_prices():
    entry = _not_low_risk(catalog_channel=False)
    with pytest.raises(SourceRegistryError, match="example-api-pricing: registry-only channel 'openai-api' is a catalog channel"):
        validate_source_registry(parse_source_registry(_registry(entry)), _catalog())
