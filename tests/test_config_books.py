"""Validates the books section of config.json and the load_books() API."""
import pytest
from src.config import load_books


VALID_TYPES    = {"exchange", "sportsbook"}
VALID_LICENSES = {"UK", "non-UK"}


@pytest.fixture(scope="module")
def books():
    return load_books()


def test_books_not_empty(books):
    assert len(books) > 0


def test_all_required_keys_present(books):
    required = {"key", "label", "type", "license", "commission_rate"}
    for b in books:
        assert required <= b.keys(), f"Book missing keys: {b}"


def test_keys_unique(books):
    keys = [b["key"] for b in books]
    assert len(keys) == len(set(keys)), "Duplicate book keys found"


def test_type_values(books):
    for b in books:
        assert b["type"] in VALID_TYPES, f"Unknown type for {b['key']}: {b['type']}"


def test_license_values(books):
    for b in books:
        assert b["license"] in VALID_LICENSES, f"Unknown license for {b['key']}: {b['license']}"


def test_commission_rate_range(books):
    for b in books:
        rate = b["commission_rate"]
        assert isinstance(rate, (int, float)), f"commission_rate not numeric for {b['key']}"
        assert 0.0 <= rate <= 1.0, f"commission_rate out of range for {b['key']}: {rate}"


def test_exchanges_have_uk_license(books):
    for b in books:
        if b["type"] == "exchange":
            assert b["license"] == "UK", f"Exchange {b['key']} not UK-licensed"


def test_known_books_present(books):
    keys = {b["key"] for b in books}
    for expected in ("betfair_ex_uk", "smarkets", "matchbook", "pinnacle", "williamhill"):
        assert expected in keys, f"Expected book missing: {expected}"


def test_uk_licensed_set_derived_correctly(books):
    from src.betting.strategies import UK_LICENSED_BOOKS, EXCHANGE_BOOKS
    uk = {b["key"] for b in books if b["license"] == "UK"}
    ex = {b["key"] for b in books if b["type"] == "exchange"}
    assert UK_LICENSED_BOOKS == uk
    assert EXCHANGE_BOOKS == ex
