"""One bounded real-model proposal + automatic application, checks and exact reversal.

Uses Luna for this technical test only. Never changes the selected model or production.
The technical case is reverted and is not admitted to approved case memory.
"""
import argparse
import json
import sys
from pathlib import Path
import httpx

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
parser = argparse.ArgumentParser()
parser.add_argument("--execute", action="store_true")
args = parser.parse_args()
if not args.execute:
    raise SystemExit("Use --execute para autorizar o teste reversível em homologação e uma chamada real ao Luna.")

from app import provider
from app.model_settings import read_config

config = read_config()
if not config.get("api_key"):
    raise SystemExit("Configure a OpenAI antes do teste real")
test_config = {**config, "model": "gpt-6-luna", "max_completion_tokens": 4096}
provider.read_config = lambda: test_config
lines = (root / ".local-access.txt").read_text(encoding="utf-8").splitlines()
password = next(line.split(": ", 1)[1] for line in lines if line.startswith("Senha:"))
username = next((line.split(": ", 1)[1] for line in lines if line.startswith("Usuário:")), "consultor")
case_id = None
report = {"model": "gpt-6-luna", "production_changed": False, "technical_case_not_training_data": True}
with httpx.Client(base_url="http://localhost:3001", headers={"Origin": "http://localhost:3001"}, timeout=60) as client:
    def call(path, payload=None):
        response = client.post("/api" + path, json=payload) if payload is not None else client.get("/api" + path)
        response.raise_for_status()
        return response.json()
    call("/login", {"username": username, "password": password})
    connection = call("/connection?company_code=EMP001")
    baseline = connection["snapshot"]
    current = baseline["effective"]["homologation"][0]["configuration"]["min_advance_days"]
    target = current + 1 if current < 365 else 364
    request = f"Teste técnico reversível: alterar somente a antecedência mínima da regra geral da Empresa ABC de {current} para {target} dias em homologação, na vigência {baseline['as_of']}. Preservar todos os outros parâmetros e as regras próprias de grupos. Não publicar em produção."
    try:
        proposal, metrics = provider.generate(request, baseline, [{"id": "catalog:" + connection["catalog"]["version"], "content": connection["catalog"]}], [])
        assert proposal is not None and not proposal.questions
        assert len(proposal.changes) == 1 and proposal.changes[0].group_id is None and proposal.changes[0].parameter == "min_advance_days" and proposal.changes[0].value == target, "Modelo não produziu a alteração exata; nada será aplicado"
        report["model_metrics"] = metrics
        row = call("/cases", {"request": request, "effective_from": baseline["as_of"], "company_code": baseline["company"]["code"]})
        case_id = row["id"]
        row = call(f"/cases/{case_id}/capture", {"revision": row["revision"]})
        assert row["snapshot"]["schedules"] == baseline["schedules"] and row["snapshot"]["groups"] == baseline["groups"], "HCM mudou durante a geração; abortar sem alterações"
        row = call(f"/cases/{case_id}/manual-proposal", {"revision": row["revision"], "proposal": proposal.model_dump(), "comment": "Teste técnico: saída de modelo real Luna. Será revertido e excluído da memória aprovada."})
        row = call(f"/cases/{case_id}/execute", {"revision": row["revision"], "approval_hash": row["approval_hash"]})
        assert row["status"] == "applied" and row["result"]["automatic_tests"]["passed"]
        row = call(f"/cases/{case_id}/verify", {"revision": row["revision"]})
        assert row["result"]["configuration_matches"] and row["result"]["production_unchanged"]
        report["operation_id"] = row["result"]["operation_id"]
        report["automatic_check_count"] = len(row["result"]["automatic_tests"]["checks"])
        report["policy_probe_count"] = sum(len(check.get("probes", [])) for check in row["result"]["automatic_tests"]["checks"])
        row = call(f"/cases/{case_id}/rollback", {"revision": row["revision"], "comment": "Reversão obrigatória do teste técnico; preservar as parametrizações do consultor."})
        assert row["status"] == "reverted"
        after = call("/connection?company_code=EMP001")["snapshot"]
        assert after["schedules"] == baseline["schedules"]
        report.update({"passed": True, "case_id": case_id, "restored_exact_baseline": True})
    finally:
        if case_id is not None:
            latest = call(f"/cases/{case_id}")
            if latest["status"] in ("applied", "verification_failed", "validated"):
                call(f"/cases/{case_id}/rollback", {"revision": latest["revision"], "comment": "Limpeza segura do teste técnico interrompido."})
        call("/logout", {})
(root / "artifacts").mkdir(exist_ok=True)
(root / "artifacts" / "live-acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
