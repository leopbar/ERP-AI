"""Reversible real-HCM acceptance of the contract v2 named-configuration path, without any model call.

Creates a named vacation configuration through a consultant-authored plan, assigns it to a group, applies in
homologation, verifies that the group's effective rule now comes from the assignment, confirms, reverts and
checks the exact baseline. The technical case ends reverted, so it never enters approved memory."""
import argparse
import json
from pathlib import Path
from urllib.parse import quote
import httpx

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--execute", action="store_true")
parser.add_argument("--company-query", default="ABC")
args = parser.parse_args()
if not args.execute:
    raise SystemExit("Use --execute para o teste reversível de configuração nomeada em homologação.")
password = next(line.split(": ", 1)[1] for line in (root / ".local-access.txt").read_text(encoding="utf-8").splitlines() if line.startswith("Senha:"))
username = next((line.split(": ", 1)[1] for line in (root / ".local-access.txt").read_text(encoding="utf-8").splitlines() if line.startswith("Usuário:")), "consultor")
report = {"passed": False, "kind": "named_configuration"}
case_id = None
with httpx.Client(base_url="http://localhost:3001", headers={"Origin": "http://localhost:3001"}, timeout=120) as client:
    def call(path, payload=None):
        response = client.get("/api" + path) if payload is None else client.post("/api" + path, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"{path}: {response.status_code}: {response.json().get('detail')}")
        return response.json()
    def action(row, endpoint, **kwargs):
        return call(f"/cases/{row['id']}/{endpoint}", {"revision": row["revision"], **kwargs})
    call("/login", {"username": username, "password": password})
    matches = call("/companies?query=" + quote(args.company_query))["items"]
    assert len(matches) == 1, "Informe um nome de empresa sem ambiguidade."
    company = matches[0]
    connection = call("/connection?company_code=" + quote(company["code"]))
    baseline = connection["snapshot"]
    group = baseline["groups"][0]
    code = "ERPAI-TMP"
    assert code not in {item["config_code"] for item in baseline["named_configurations"]["homologation"]}, "Código temporário já existe; limpe antes."
    current = baseline["effective"]["homologation"][0]["configuration"]
    target = 3 if current["max_installments"] != 3 else 2
    try:
        row = call("/cases", {"request": f"Teste técnico reversível: criar a configuração nomeada {code} com {target} parcelas e atribuí-la ao grupo {group['name']} da empresa {company['name']}.", "effective_from": baseline["as_of"], "company_code": company["code"]})
        case_id = row["id"]
        row = action(row, "capture")
        proposal = {"summary": f"Configuração nomeada {code} com {target} parcelas atribuída ao grupo {group['name']}", "questions": [],
                    "changes": [{"config_code": code, "parameter": "max_installments", "value": target, "reason": "Teste técnico do contrato v2"}],
                    "tests": ["Regra efetiva do grupo vem da atribuição", "Regra geral preservada", "Produção preservada"], "sources": ["catalog:" + connection["catalog"]["version"]],
                    "named_configurations": [{"config_code": code, "name": "Temporária do ERP AI", "simulation_text": None}],
                    "assignments": {"vacations": [{"group": group["name"], "config": code}, *baseline["assignments"]["homologation"]["vacations"]], "vacations_simulation": baseline["assignments"]["homologation"]["vacations_simulation"]}}
        row = action(row, "manual-proposal", proposal=proposal, comment="Teste técnico: plano de autoria do script, será revertido.")
        assert row["proposal"]["payloads"][0]["config_code"] == code and row["proposal"]["assignments"]["vacations"][0]["config"] == code
        row = action(row, "execute", approval_hash=row["approval_hash"])
        assert row["status"] == "applied" and row["result"]["automatic_tests"]["passed"], row["result"].get("automatic_tests")
        effective = next(item for item in row["result"]["execution"]["after_snapshot"]["effective"]["homologation"] if item["group_id"] == group["id"])
        assert effective["origin"]["scope"] == "assignment" and effective["configuration"]["config_code"] == code and effective["configuration"]["max_installments"] == target
        assert row["result"]["execution"]["after_snapshot"]["effective"]["homologation"][0]["configuration"]["max_installments"] == current["max_installments"]
        report.update(checks=len(row["result"]["automatic_tests"]["checks"]), operation_id=row["result"]["operation_id"])
        row = action(row, "verify")
        assert row["result"]["configuration_matches"] and row["result"]["assignments_match"] and row["result"]["production_unchanged"]
        row = action(row, "confirm-result", comment="Validação técnica do caminho de configuração nomeada; reverter em seguida.")
        assert row["status"] == "validated"
        row = action(row, "rollback", comment="Reversão do teste técnico de configuração nomeada.")
        assert row["status"] == "reverted"
        after = call("/connection?company_code=" + quote(company["code"]))["snapshot"]
        assert after["fingerprint"] == baseline["fingerprint"], "Baseline não restaurada exatamente"
        report.update(passed=True, case_id=case_id, company=company["name"], group=group["name"], restored_exact_baseline=True, production_unchanged=True)
    finally:
        if case_id is not None:
            row = call(f"/cases/{case_id}")
            if row["status"] in ("applied", "validated", "verification_failed", "rollback_unknown"):
                action(row, "rollback", comment="Limpeza obrigatória do teste técnico de configuração nomeada.")
            elif row["status"] not in ("reverted", "rejected", "consultant_closed"):
                action(row, "review", decision="reject", comment="Teste técnico encerrado sem aprovação de solução de negócio.")
        (root / "artifacts").mkdir(exist_ok=True)
        (root / "artifacts" / "live-named-config.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=True))
