import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_prices.py"
spec = importlib.util.spec_from_file_location("update_prices", MODULE_PATH)
update_prices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_prices)


def test_extract_price_from_json_ld_offer():
    html = '''
    <html><head><script type="application/ld+json">
    {"@type":"Product","offers":{"@type":"Offer","price":"91.42","priceCurrency":"USD"}}
    </script></head></html>
    '''
    assert update_prices.extract_product_price(html, expected_spot=75.0) == 91.42


def test_extract_price_from_visible_as_low_as_text():
    html = '<div class="product-price">As Low As $88.37</div>'
    assert update_prices.extract_product_price(html, expected_spot=75.0) == 88.37


def test_rejects_spot_quote_when_product_price_expected():
    html = '<span class="price">$76.36</span><div>Silver Ask</div>'
    assert update_prices.extract_product_price(html, expected_spot=75.62) is None


def test_build_snapshot_uses_live_dealer_price_when_available():
    spot = {"source": "test", "silver": 75.0, "gold": None, "platinum": None, "palladium": None}
    dealers = {
        "jm": {
            "name": "JM Bullion",
            "eaglePremium": 10.0,
            "barPremium": 5.0,
            "url": "https://example.com",
            "products": {
                "eagle": {"url": "https://example.com/eagle"},
                "bar": {"url": "https://example.com/bar"},
            },
        }
    }
    dealer_prices = {"jm": {"eagle": 90.0, "bar": None}}

    snapshot = update_prices.build_snapshot(spot, dealers, dealer_prices=dealer_prices)
    jm = snapshot["dealers"]["jm"]

    assert jm["eagle"] == 90.0
    assert jm["eaglePremium"] == 15.0
    assert jm["eagleSource"] == "dealer-live"
    assert jm["bar"] == 80.0
    assert jm["barPremium"] == 5.0
    assert jm["barSource"] == "manual-premium"
    assert jm["products"]["eagle"]["url"] == "https://example.com/eagle"


def test_fetch_dealer_prices_keeps_none_on_fetch_failure():
    dealers = {
        "jm": {
            "name": "JM Bullion",
            "products": {"eagle": {"url": "https://example.invalid/eagle"}},
        }
    }

    def failing_fetch(_url):
        raise TimeoutError("boom")

    results, errors = update_prices.fetch_dealer_prices(dealers, 75.0, fetch_html=failing_fetch)

    assert results == {"jm": {"eagle": None}}
    assert "jm.eagle" in errors
    assert "boom" in errors["jm.eagle"]
