"""Backend tests for India Macro Lens live data proxy endpoints."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://indicator-explorer-1.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"

# Upstream APIs (DBnomics + World Bank) can be slow. Give plenty of headroom.
LONG_TIMEOUT = 60


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ── Existing baseline endpoints ─────────────────────────────────────────────
class TestBaselineEndpoints:
    def test_root(self, session):
        r = session.get(f"{API}/", timeout=15)
        assert r.status_code == 200, r.text
        assert r.json() == {"message": "Hello World"}

    def test_status_post_and_list(self, session):
        payload = {"client_name": "TEST_macro_proxy"}
        r = session.post(f"{API}/status", json=payload, timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["client_name"] == payload["client_name"]
        assert "id" in body and isinstance(body["id"], str) and len(body["id"]) > 0
        assert "timestamp" in body

        # Verify persisted in list
        r2 = session.get(f"{API}/status", timeout=15)
        assert r2.status_code == 200
        items = r2.json()
        assert isinstance(items, list)
        assert any(it.get("client_name") == "TEST_macro_proxy" for it in items)


# ── Macro proxy endpoints ───────────────────────────────────────────────────
EXPECTED_MONTHLY = {"inflation", "forex", "industrial_prod", "exports"}
EXPECTED_ANNUAL = {"gdp_growth", "gfcf", "unemployment", "consumer_spending"}
EXPECTED_KEYS = EXPECTED_MONTHLY | EXPECTED_ANNUAL


class TestMacroIndicators:
    @pytest.fixture(scope="class")
    def all_indicators(self, session):
        r = session.get(f"{API}/macro/indicators", timeout=LONG_TIMEOUT)
        assert r.status_code == 200, r.text
        return r.json()

    def test_returns_indicator_object(self, all_indicators):
        assert isinstance(all_indicators, dict)
        assert "indicators" in all_indicators
        assert isinstance(all_indicators["indicators"], dict)
        assert len(all_indicators["indicators"]) > 0, (
            f"indicators dict is empty. errors={all_indicators.get('errors')}"
        )

    def test_required_indicator_keys_present(self, all_indicators):
        keys = set(all_indicators["indicators"].keys())
        missing = EXPECTED_KEYS - keys
        errors = all_indicators.get("errors", {})
        assert not missing, (
            f"Missing indicator keys: {missing}. errors={errors}, present={keys}"
        )

    def test_indicator_structure_and_history(self, all_indicators):
        for k in EXPECTED_KEYS:
            ind = all_indicators["indicators"].get(k)
            assert ind is not None, f"{k} missing"
            # numeric value
            assert isinstance(ind.get("value"), (int, float)), (
                f"{k}.value not numeric: {ind.get('value')!r}"
            )
            # non-empty period
            assert isinstance(ind.get("period"), str) and ind["period"], (
                f"{k}.period invalid: {ind.get('period')!r}"
            )
            # non-empty source
            assert isinstance(ind.get("source"), str) and ind["source"], (
                f"{k}.source invalid"
            )
            hist = ind.get("history")
            assert isinstance(hist, list) and len(hist) >= 5, (
                f"{k}.history must have >=5 items, got {len(hist) if isinstance(hist, list) else 'N/A'}"
            )
            for pt in hist:
                assert "period" in pt and pt["period"], f"{k} history point missing period"
                assert isinstance(pt.get("value"), (int, float)), (
                    f"{k} history point value not numeric: {pt}"
                )

    def test_monthly_sources_mention_dbnomics(self, all_indicators):
        for k in EXPECTED_MONTHLY:
            ind = all_indicators["indicators"].get(k)
            assert ind is not None, f"{k} missing"
            assert "DBnomics" in ind["source"], (
                f"{k}.source should mention DBnomics, got {ind['source']}"
            )

    def test_annual_sources_world_bank(self, all_indicators):
        for k in EXPECTED_ANNUAL:
            ind = all_indicators["indicators"].get(k)
            assert ind is not None, f"{k} missing"
            assert "World Bank" in ind["source"], (
                f"{k}.source should mention World Bank, got {ind['source']}"
            )

    def test_caching_behavior(self, session, all_indicators):
        # First fetch (fixture) should be cached: False on cold start,
        # but if a previous test/run already warmed it the field may be True.
        # Either way a fast second call MUST return cached True.
        time.sleep(1)
        r2 = session.get(f"{API}/macro/indicators", timeout=LONG_TIMEOUT)
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2.get("cached") is True, (
            f"Second call should be cached, got cached={body2.get('cached')}"
        )
        assert isinstance(body2.get("cache_age_s"), int) and body2["cache_age_s"] >= 0, (
            f"cache_age_s invalid: {body2.get('cache_age_s')!r}"
        )


class TestMacroSingleIndicator:
    def test_inflation_indicator(self, session):
        r = session.get(f"{API}/macro/indicators/inflation", timeout=LONG_TIMEOUT)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("id") == "inflation"
        assert "DBnomics" in body.get("source", "")
        assert isinstance(body.get("value"), (int, float))
        assert isinstance(body.get("history"), list) and len(body["history"]) >= 5

    def test_unknown_indicator_returns_404(self, session):
        r = session.get(
            f"{API}/macro/indicators/this_does_not_exist", timeout=LONG_TIMEOUT
        )
        assert r.status_code == 404, r.text
        body = r.json()
        # FastAPI returns {"detail": "..."}
        detail = body.get("detail", "")
        assert isinstance(detail, str) and len(detail) > 0
        assert "this_does_not_exist" in detail or "no live data" in detail.lower()
