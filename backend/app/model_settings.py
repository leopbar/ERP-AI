"""Server-only provider configuration; public responses never contain credentials."""
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from fastapi import HTTPException
from pydantic import Field, SecretStr
from app.contracts import StrictModel

DEFAULT_MODEL = "gpt-6-luna"
OPENAI_BASE = "https://api.openai.com/v1"
lock = RLock()

class ModelSettingsInput(StrictModel):
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    api_key: SecretStr | None = None
    max_completion_tokens: int = Field(default=4096, ge=512, le=16384)

def config_path():
    return Path(os.getenv("LLM_CONFIG_FILE", str(Path(__file__).resolve().parents[2] / "secrets" / "model.json")))

def read_config():
    path = config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not all(isinstance(data.get(key), str) for key in ("base_url", "model", "api_key")):
                raise ValueError()
            return data
        except (OSError, ValueError):
            raise HTTPException(503, "Arquivo local do modelo inválido; revise a configuração no servidor")
    return {"base_url": os.getenv("LLM_BASE_URL", ""), "model": os.getenv("LLM_MODEL", ""), "api_key": os.getenv("LLM_API_KEY", ""), "max_completion_tokens": 4096}

def write_config(config):
    path = config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(config, stream, ensure_ascii=False)
        os.replace(temporary, path)
    except OSError as exc:
        raise HTTPException(503, "Não foi possível salvar a configuração no armazenamento do backend") from exc

def public_settings():
    config = read_config()
    return {"provider": "OpenAI" if config["base_url"] == OPENAI_BASE else "Compatível" if config["base_url"] else "OpenAI",
            "model": config["model"] or DEFAULT_MODEL,
            "configured": bool(config["base_url"] and config["model"] and config["api_key"]),
            "api_key_configured": bool(config["api_key"]),
            "max_completion_tokens": config.get("max_completion_tokens", 4096), "last_test": config.get("last_test")}

def save_settings(payload):
    with lock:
        existing = read_config()
        key = payload.api_key.get_secret_value().strip() if payload.api_key else ""
        if not key:
            if existing["base_url"] != OPENAI_BASE or not existing["api_key"]:
                raise HTTPException(422, "Informe sua chave de API da OpenAI")
            key = existing["api_key"]
        if len(key) < 20 or re.search(r"\s", key):
            raise HTTPException(422, "A chave informada parece incompleta ou contém espaços")
        write_config({"base_url": OPENAI_BASE, "model": payload.model, "api_key": key,
                      "max_completion_tokens": payload.max_completion_tokens, "last_test": None})
    return public_settings()

def record_test(config, report):
    with lock:
        current = read_config()
        if all(current.get(key) == config.get(key) for key in ("api_key", "model", "base_url")):
            write_config({**current, "last_test": report})
