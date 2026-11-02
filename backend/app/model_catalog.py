"""Curated compatible models. /models supplies account listing, NOT pricing."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import hashlib
import time
import httpx

SOURCE = "https://developers.openai.com/api/docs/pricing"
CHECKED_AT = "2026-10-31"
MODELS = [
    {"id": "gpt-6-luna", "name": "GPT-6 Luna", "input_usd": 0.10, "output_usd": 0.50, "cached_input_usd": 0.01,
     "recommendation": "Comece por este · econômico", "recommended": True,
     "description": "Primeira opção para o fluxo de Férias, com catálogo, regras determinísticas e revisão humana. Avalie os acertos antes de ampliar o uso."},
    {"id": "gpt-6.1-sol", "name": "GPT-6.1 Sol", "input_usd": 2.00, "output_usd": 10.00, "cached_input_usd": 0.10,
     "recommendation": "Recomendado para casos complexos", "recommended": True,
     "description": "Opção para comparar em requisitos com várias dependências, exceções de grupo ou ambiguidades. Exige avaliação funcional como os demais."},
    {"id": "gpt-6.1-terra", "name": "GPT-6.1 Terra", "input_usd": 1.00, "output_usd": 5.00, "cached_input_usd": 0.05,
     "recommendation": "Alternativa intermediária", "recommended": False,
     "description": "Custo entre Luna e Sol. Pode ser comparado no sistema; suas capacidades visuais não são essenciais para a atual integração por API."},
    {"id": "gpt-6-astra", "name": "GPT-6 Astra", "input_usd": 10.00, "output_usd": 50.00, "cached_input_usd": 1.00,
     "recommendation": "Uso excepcional · maior custo", "recommended": False,
     "description": "Referência de capacidade para comparações pontuais. Não é necessário usá-lo como padrão neste sistema."},
]

def previous_model(model_id, name, input_rate, output_rate, cached_rate, shutdown=None, reasoning=True):
    return {"id": model_id, "name": name, "input_usd": input_rate, "output_usd": output_rate, "cached_input_usd": cached_rate, "reasoning": reasoning,
            "recommendation": "Anterior · comparar com atuais" if shutdown is None else "Anterior · retirada anunciada",
            "recommended": False, "shutdown_date": shutdown,
            "description": "Modelo anterior compatível com propostas estruturadas. Compare custo, correções e tempo no mesmo conjunto de cenários antes de adotar."}

# Verified from individual official model pages and /api/docs/deprecations on 2026-10-31.
# Deprecated means still callable until shutdown, not already shut down.
MODELS += [
    previous_model("gpt-6-sol", "GPT-6 Sol", 2.00, 10.00, 0.20),
    previous_model("gpt-5.6-sol", "GPT-5.6 Sol", 4.00, 20.00, 0.40),
    previous_model("gpt-5.6-terra", "GPT-5.6 Terra", 2.00, 12.00, 0.20),
    previous_model("gpt-5.6-luna", "GPT-5.6 Luna", 0.20, 1.20, 0.02),
    previous_model("gpt-5.5", "GPT-5.5", 5.00, 30.00, 0.50),
    previous_model("gpt-5.4", "GPT-5.4", 2.50, 15.00, 0.25),
    previous_model("gpt-5.4-mini", "GPT-5.4 mini", 0.75, 4.50, 0.075),
    previous_model("gpt-5.4-nano", "GPT-5.4 nano", 0.20, 1.25, 0.02, "2027-04-01"),
    previous_model("gpt-5.2", "GPT-5.2", 1.75, 14.00, 0.175),
    previous_model("gpt-5.1", "GPT-5.1", 1.25, 10.00, 0.125, "2027-04-01"),
    previous_model("gpt-5", "GPT-5", 1.25, 10.00, 0.125, "2026-12-11"),
    previous_model("gpt-5-mini", "GPT-5 mini", 0.25, 2.00, 0.025, "2026-12-11"),
    previous_model("gpt-5-nano", "GPT-5 nano", 0.05, 0.40, 0.005, "2026-12-11"),
    previous_model("gpt-4.1", "GPT-4.1", 2.00, 8.00, 0.50, "2027-01-14", reasoning=False),
    previous_model("gpt-4.1-mini", "GPT-4.1 mini", 0.40, 1.60, 0.10, "2027-01-14", reasoning=False),
    previous_model("gpt-4o", "GPT-4o", 2.50, 10.00, 1.25, "2027-01-14", reasoning=False),
    previous_model("gpt-4o-mini", "GPT-4o mini", 0.15, 0.60, 0.075, "2027-01-14", reasoning=False),
]
for _model in MODELS:
    _model.setdefault("reasoning", True)  # current generations accept reasoning_effort; the catalog, not a name prefix, decides

def supports_reasoning_effort(model_id):
    """Whether the request may carry reasoning_effort. Unknown models (compatible providers) get no such parameter."""
    model = next((item for item in MODELS if item["id"] == model_id), None)
    return bool(model and model.get("reasoning"))
DEPRECATION_SOURCE = "https://developers.openai.com/api/docs/deprecations"

def active_models(today=None):
    today = today or datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    return [model for model in MODELS if not model.get("shutdown_date") or date.fromisoformat(model["shutdown_date"]) > today]

_listing_cache = {}

def list_models(config):
    ids = None
    status = "not_configured"
    checked_at = None
    if config.get("api_key") and config.get("base_url") == "https://api.openai.com/v1":
        cache_key = hashlib.sha256(config["api_key"].encode()).hexdigest()
        cached = _listing_cache.get(cache_key)
        if cached and time.monotonic() - cached["time"] < 60:
            ids, status, checked_at = cached["ids"], cached["status"], cached["checked_at"]
        else:
            checked_at = datetime.now(timezone.utc).isoformat()
            try:
                with httpx.Client(timeout=10, follow_redirects=False) as client:
                    response = client.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + config["api_key"]})
                response.raise_for_status()
                ids = {row["id"] for row in response.json()["data"]}
                status = "checked"
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                status = "unavailable"
            _listing_cache.clear()
            _listing_cache[cache_key] = {"time": time.monotonic(), "ids": ids, "status": status, "checked_at": checked_at}
    return {"currency": "USD", "unit_tokens": 1000000, "processing": "Standard", "pricing_checked_at": CHECKED_AT,
            "pricing_source": SOURCE, "availability_status": status, "availability_checked_at": checked_at,
            "availability_note": "Listado pela conta não garante acesso em todas as operações. Confirme com o teste de conexão.",
            "recommendation_note": "Recomendações de arquitetura para o sistema; ainda não são resultado de avaliação comparativa no HCM.",
            "deprecation_source": DEPRECATION_SOURCE,
            "excluded_note": "Modelos desligados e variantes especializadas sem suporte ao contrato desta integração não são oferecidos.",
            "models": [{**model, "generation": "4" if model["id"].startswith("gpt-4") else "5" if model["id"].startswith("gpt-5") else "6", "reasoning": bool(model.get("reasoning")),
                        "lifecycle": "retiring" if model.get("shutdown_date") else "active",
                        "listed_in_account": model["id"] in ids if ids is not None else None,
                        "documentation": "https://developers.openai.com/api/docs/models/" + model["id"],
                        "pricing_source": "https://developers.openai.com/api/docs/models/" + model["id"]} for model in active_models()]}

def estimate_cost(model_id, usage):
    model = next((item for item in MODELS if item["id"] == model_id), None)
    if not model or not isinstance(usage, dict) or not all(type(usage.get(key)) is int for key in ("prompt_tokens", "completion_tokens")):
        return None
    prompt, output = usage["prompt_tokens"], usage["completion_tokens"]
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    if type(cached) is not int or not 0 <= cached <= prompt or prompt < 0 or output < 0:
        return None
    amount = ((prompt - cached) * model["input_usd"] + cached * model["cached_input_usd"] + output * model["output_usd"]) / 1000000
    return {"estimated_usd": round(amount, 8), "currency": "USD", "pricing_checked_at": CHECKED_AT, "pricing_source": SOURCE,
            "rates_per_million": {key: model[key] for key in ("input_usd", "cached_input_usd", "output_usd")},
            "basis": "Consumo reportado; preços Standard/contexto curto. Exclui impostos, câmbio, escrita de cache e serviços extras. Não é a fatura."}
