"""Bounded real-provider acceptance of LangGraph + HCM, with exact cleanup.

Run --execute --pause-after-guide, restart backend, then --execute --resume
to also exercise durable Postgres checkpoints across process replacement.
"""
import argparse
import json
from pathlib import Path
import httpx
from urllib.parse import quote

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--execute", action="store_true")
parser.add_argument("--pause-after-guide", "--pause-at-interpretations", dest="pause_after_guide", action="store_true")
parser.add_argument("--resume", action="store_true")
parser.add_argument("--company-query", default="ABC")
args = parser.parse_args()
if not args.execute:
    raise SystemExit("Use --execute para o teste técnico reversível com o modelo real.")
artifact = root / "artifacts" / "live-orchestration.json"
artifact.parent.mkdir(exist_ok=True)
lines = (root / ".local-access.txt").read_text(encoding="utf-8").splitlines()
password = next(line.split(": ", 1)[1] for line in lines if line.startswith("Senha:"))
report = json.loads(artifact.read_text(encoding="utf-8")) if args.resume else {"passed": False, "provider": "real", "model": "gpt-6-luna"}
with httpx.Client(base_url="http://localhost:3001", headers={"Origin": "http://localhost:3001"}, timeout=150) as client:
    def call(path, payload=None):
        r = client.post("/api" + path, json=payload) if payload is not None else client.get("/api" + path)
        if r.status_code >= 400:
            raise RuntimeError(f"{path}: {r.status_code}: {r.json().get('detail')}")
        return r.json()
    def action(row, endpoint, **kwargs):
        return call(f"/cases/{row['id']}/{endpoint}", {"revision": row["revision"], **kwargs})
    call("/login", {"username": "consultor", "password": password})
    if not args.resume:
        matches = call("/companies?query=" + quote(args.company_query))["items"]
        assert len(matches) == 1, "Informe um nome de empresa sem ambiguidade para o teste."
        company = matches[0]
        baseline = call("/connection?company_code=" + quote(company["code"]))["snapshot"]
        current = baseline["effective"]["homologation"][0]["configuration"]["min_advance_days"]
        target = current + 1 if current < 365 else 364
        request = f"Teste técnico reversível de férias na empresa {company['name']}: alterar somente a antecedência mínima da regra geral para {target} dias. Pedidos com {target} dias ou mais antes do início devem ser aceitos; com menos, bloqueados. Manter regras próprias de grupos e todos os demais parâmetros."
        row = call("/cases", {"request": request, "effective_from": baseline["as_of"]})
        report.update(case_id=row["id"], baseline=baseline, target=target)
        artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        row = action(row, "orchestrate", primary_model="gpt-6-luna", escalation_model="gpt-6.1-sol")
        assert row["status"] == "awaiting_interpretation", row["status"]
        report["thread_id"] = row["workflow"]["thread_id"]
        report["classification_passed"] = row["workflow"]["module"] == "vacations"
        assert report["classification_passed"]
        if args.pause_after_guide:
            report["paused_for_restart"] = True
            artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"case_id": row["id"], "status": row["status"], "paused_for_restart": True}))
            raise SystemExit(0)
    else:
        row = call(f"/cases/{report['case_id']}")
        assert row["workflow"]["thread_id"] == report["thread_id"] and row["status"] == "awaiting_interpretation"
        report["resumed_after_restart"] = True
    try:
        assert row["status"] == "awaiting_interpretation"
        assert len(row["workflow"]["options"]) == 3
        report["options"] = row["workflow"]["options"]
        best = row["workflow"]["options"][0]
        assert best["support"] == "supported", "Primeira interpretação não é suportada: abortar sem alterar o HCM."
        row = action(row, "workflow", action="choose", choice=best["id"])
        assert row["status"] == "review", row.get("workflow", {}).get("reason")
        changes = row["proposal"]["changes"]
        assert len(changes) == 1 and changes[0]["group_id"] is None and changes[0]["parameter"] == "min_advance_days" and changes[0]["value"] == report["target"], "Plano não corresponde à alteração exata."
        report["metrics"] = row["workflow"]["metrics"]
        assert len(report["metrics"]) == 3
        assert [m["role"] for m in report["metrics"]] == ["module_classifier", "interpretation_agent", "configuration_agent"]
        row = action(row, "execute", approval_hash=row["approval_hash"])
        assert row["status"] == "applied" and row["result"]["automatic_tests"]["passed"]
        report["automatic_checks"] = len(row["result"]["automatic_tests"]["checks"])
        row = action(row, "verify")
        assert row["result"]["configuration_matches"] and row["result"]["production_unchanged"]
        row = action(row, "confirm-result", comment="Validação técnica do fluxo completo. Caso de teste será revertido imediatamente.")
        assert row["status"] == "validated"
        row = action(row, "rollback", comment="Reversão do teste técnico LangGraph; restaurar o estado anterior.")
        assert row["status"] == "reverted"
        assert call("/connection?company_code=" + quote(report["baseline"]["company"]["code"]))["snapshot"]["schedules"] == report["baseline"]["schedules"]
        report.update(passed=True, final_status=row["status"], restored_exact_baseline=True, production_unchanged=True)
    finally:
        row = call(f"/cases/{report['case_id']}")
        if row["status"] in ("applied", "verification_failed", "validated", "executing", "execution_unknown", "rollback_unknown"):
            if row["status"] in ("executing", "execution_unknown"):
                row = action(row, "execute", approval_hash=row["approval_hash"])
            row = action(row, "rollback", comment="Limpeza obrigatória do teste técnico LangGraph.")
        if row["status"] not in ("reverted", "rejected", "consultant_closed"):
            action(row, "review", decision="reject", comment="Teste técnico encerrado; não usar como solução validada.")
        for memory in call("/interpretation-memories"):
            if memory["case_id"] == report["case_id"] and memory["active"]:
                call(f"/interpretation-memories/{memory['id']}/forget", {})
        artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k not in ("baseline", "options")}, ensure_ascii=True))
