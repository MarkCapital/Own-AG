from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text()


def test_live_dealer_note_keeps_affiliate_disclosure_after_price_refresh():
    assert "OwnAG may earn a commission from dealer links at no cost to you" in HTML
    assert "lookupNote.textContent = liveDealerCells > 0" in HTML


def test_best_silver_deals_page_is_in_navigation_and_has_dynamic_targets():
    assert "showPage('deals')" in HTML
    assert 'id="page-deals"' in HTML
    assert 'id="deals-table-body"' in HTML
    assert 'id="deal-best-eagle"' in HTML
    assert 'id="deal-best-bar"' in HTML
    assert "function renderDealsPage()" in HTML


def test_deals_page_includes_monetized_ctas_and_disclosures():
    assert "Best Silver Deals Today" in HTML
    assert "Open a Silver IRA" in HTML
    assert "equitytrustcompany380f9.referralrock.com" in HTML
    assert "Affiliate disclosure" in HTML


def test_deals_page_does_not_include_placeholder_alert_signup():
    assert "Get Weekly Silver Deal Alerts" not in HTML
    assert "Request Alerts" not in HTML
    assert "repeat buyer list" not in HTML
    assert "newsletter support" not in HTML
