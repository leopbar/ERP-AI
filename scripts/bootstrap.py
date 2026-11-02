"""Provision local pilot secrets without printing them or overwriting existing secrets."""
import argparse
import hashlib
from pathlib import Path
import re
import secrets

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--hcm-path", type=Path, default=root.parent / "HCM")
args = parser.parse_args()
env_path = root / ".env"
if env_path.exists():
    values = dict(line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
    token = values["HCM_INTEGRATION_TOKEN"]
else:
    token = secrets.token_urlsafe(48)
    password = secrets.token_urlsafe(16)
    salt = secrets.token_hex(16)
    encoded = salt + ":" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200000).hex()
    env_path.write_text("\n".join([
        "DATABASE_PASSWORD=" + secrets.token_hex(24), "SESSION_SECRET=" + secrets.token_hex(32),
        "CONSULTANT_USER=consultor", "CONSULTANT_PASSWORD_HASH=" + encoded,
        "HCM_INTEGRATION_TOKEN=" + token, "HCM_BASE_URL=http://localhost:8000",
        "APP_ORIGIN=http://localhost:3001", "COOKIE_SECURE=false", "LLM_BASE_URL=", "LLM_MODEL=", "LLM_API_KEY=", ""]), encoding="utf-8")
    (root / ".local-access.txt").write_text("Acesso local: http://localhost:3001\nUsuário: consultor\nSenha: " + password + "\n", encoding="utf-8")
current_values = dict(line.split("=", 1) for line in env_path.read_text(encoding="utf-8").splitlines() if "=" in line and not line.startswith("#"))
execution_token = current_values.get("HCM_EXECUTION_TOKEN") or secrets.token_urlsafe(48)
if not current_values.get("HCM_EXECUTION_TOKEN"):
    with env_path.open("a", encoding="utf-8") as stream:
        stream.write("\nHCM_EXECUTION_TOKEN=" + execution_token + "\n")
hcm_env = args.hcm_path.resolve() / ".env"
if not (args.hcm_path / "backend/app/integration_api.py").exists():
    raise SystemExit("O HCM informado não contém o adaptador de integração. Nenhuma configuração do HCM foi alterada.")
content = hcm_env.read_text(encoding="utf-8") if hcm_env.exists() else ""
setting = "HCM_INTEGRATION_TOKEN_SHA256=" + hashlib.sha256(token.encode()).hexdigest()
content = re.sub(r"(?m)^HCM_INTEGRATION_TOKEN_SHA256=.*$", setting, content) if re.search(r"(?m)^HCM_INTEGRATION_TOKEN_SHA256=", content) else content.rstrip() + "\n" + setting + "\n"
execution_setting = "HCM_EXECUTION_TOKEN_SHA256=" + hashlib.sha256(execution_token.encode()).hexdigest()
content = re.sub(r"(?m)^HCM_EXECUTION_TOKEN_SHA256=.*$", execution_setting, content) if re.search(r"(?m)^HCM_EXECUTION_TOKEN_SHA256=", content) else content.rstrip() + "\n" + execution_setting + "\n"
hcm_env.write_text(content.lstrip("\n"), encoding="utf-8")
print("Credenciais locais preparadas. Acesso em .local-access.txt; segredos não foram exibidos.")
