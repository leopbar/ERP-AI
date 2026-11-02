import httpx
import pytest
from test_workflow import setup
from app.model_catalog import estimate_cost, list_models, active_models, _listing_cache
from datetime import date

def test_estimate_uses_rates_and_does_not_double_count_reasoning():
    usage = {"prompt_tokens": 5000, "completion_tokens": 1000, "completion_tokens_details": {"reasoning_tokens": 500}}
    assert estimate_cost("gpt-6-luna", usage)["estimated_usd"] == 0.001
    assert estimate_cost("gpt-6.1-terra", usage)["estimated_usd"] == 0.01
    assert estimate_cost("gpt-6.1-sol", usage)["estimated_usd"] == 0.02
    assert estimate_cost("gpt-6-astra", usage)["estimated_usd"] == 0.1
    cached = {**usage, "prompt_tokens_details": {"cached_tokens": 2000}}
    assert estimate_cost("gpt-6-luna", cached)["estimated_usd"] == 0.00082
    assert estimate_cost("custom-model", usage) is None
    assert estimate_cost("gpt-6-luna", {"total_tokens": 6000}) is None

def test_account_listing_is_separate_from_compatibility_and_prices(monkeypatch):
    import app.model_catalog as catalog
    _listing_cache.clear()
    config = {"base_url": "https://api.openai.com/v1", "api_key": "test-only-not-real"}
    original = httpx.Client
    calls = []
    def respond(request):
        calls.append(request.url)
        assert request.method == "GET" and request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "gpt-6-luna"}, {"id": "other-model"}]})
    monkeypatch.setattr(catalog.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    result = list_models(config)
    assert result["availability_status"] == "checked"
    assert result["models"][0]["listed_in_account"] is True
    assert next(m for m in result["models"] if m["id"] == "gpt-6-astra")["listed_in_account"] is False
    assert result["pricing_checked_at"] == "2026-10-31"
    list_models(config)
    assert len(calls) == 1
    assert config["api_key"] not in str(result)

def test_listing_failure_means_unknown_not_denied(monkeypatch):
    import app.model_catalog as catalog
    _listing_cache.clear()
    original = httpx.Client
    def respond(request):
        return httpx.Response(403, json={"error": "forbidden"})
    monkeypatch.setattr(catalog.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    result = list_models({"base_url": "https://api.openai.com/v1", "api_key": "other-test-key"})
    assert result["availability_status"] == "unavailable"
    assert all(model["listed_in_account"] is None for model in result["models"])

def test_catalog_requires_session_and_does_not_switch_saved_model(setup):
    client, _, _ = setup
    assert client.get("/api/model/catalog").status_code == 200
    assert client.get("/api/model").json()["model"] == "gpt-6-luna"
    client.cookies.clear()
    assert client.get("/api/model/catalog").status_code == 401

def test_previous_active_models_and_scheduled_retirements():
    current = {model["id"]: model for model in active_models(date(2026, 10, 31))}
    assert len(current) == 21
    assert {"gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5.4-mini", "gpt-4.1-mini", "gpt-4o-mini"} <= set(current)
    assert not {"gpt-3.5-turbo", "gpt-4", "gpt-4-turbo", "gpt-4.1-nano"} & set(current)
    assert current["gpt-5-mini"]["shutdown_date"] == "2026-12-11"
    later = {model["id"] for model in active_models(date(2026, 12, 11))}
    assert "gpt-5-mini" not in later and "gpt-4.1-mini" in later
    later = {model["id"] for model in active_models(date(2027, 1, 14))}
    assert "gpt-4.1-mini" not in later and "gpt-5.1" in later

def test_previous_models_cost_estimates():
    usage = {"prompt_tokens": 5000, "completion_tokens": 1000}
    assert estimate_cost("gpt-5-mini", usage)["estimated_usd"] == 0.00325
    assert estimate_cost("gpt-5-nano", usage)["estimated_usd"] == 0.00065
    assert estimate_cost("gpt-4.1-mini", usage)["estimated_usd"] == 0.0036
    assert estimate_cost("gpt-4o-mini", usage)["estimated_usd"] == 0.00135

def test_previous_non_reasoning_model_omits_reasoning_parameter(monkeypatch):
    from app.provider import completion
    import app.provider as provider
    original = httpx.Client
    def respond(request):
        import json
        data = json.loads(request.content)
        assert "reasoning_effort" not in data
        assert data["response_format"]["json_schema"]["strict"] is True
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})
    monkeypatch.setattr(provider.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    data, metrics = completion({"base_url": "https://api.openai.com/v1", "api_key": "test-only", "model": "gpt-4.1-mini"}, [], {"type": "object"})
    assert data == {"ok": True} and metrics["cost"] is not None
