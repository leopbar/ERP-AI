"""Real Luna -> Sol/high -> consultant acceptance. Does not write HCM settings."""
import argparse
import json
from pathlib import Path
import httpx

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--execute", action="store_true")
parser.add_argument("--pause-at-options", action="store_true")
parser.add_argument("--resume", action="store_true")
args = parser.parse_args()
if not args.execute:
    raise SystemExit("Use --execute para testar os modelos reais Luna e Sol sem alterar configurações do HCM.")
artifact = root / "artifacts" / "live-escalation.json"
artifact.parent.mkdir(exist_ok=True)
report = json.loads(artifact.read_text(encoding="utf-8")) if args.resume else {"passed": False}
lines = (root / ".local-access.txt").read_text(encoding="utf-8").splitlines()
password = next(line.split(": ", 1)[1] for line in lines if line.startswith("Senha:"))
username = next((line.split(": ", 1)[1] for line in lines if line.startswith("Usuário:")), "consultor")
with httpx.Client(base_url="http://localhost:3001", headers={"Origin": "http://localhost:3001"}, timeout=150) as client:
    def call(path, payload=None):
        r = client.post("/api" + path, json=payload) if payload is not None else client.get("/api" + path)
        r.raise_for_status()
        return r.json()
    def action(row, endpoint="workflow", **kwargs):
        return call(f"/cases/{row['id']}/{endpoint}", {"revision": row["revision"], **kwargs})
    def persist():
        artifact.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    call("/login", {"username": username, "password": password})
    if not args.resume:
        snapshot = call("/connection?company_code=EMP001")["snapshot"]
        report["baseline_schedules"] = snapshot["schedules"]
        row = call("/cases", {"request": "Teste técnico de interpretação: permitir férias apenas uma vez por ano para a ABC. O pedido deve acontecer no máximo 15 dias antes do início das férias.", "effective_from": snapshot["as_of"]})
        report["case_id"] = row["id"]
        persist()
        row = action(row, "orchestrate", primary_model="gpt-6-luna", escalation_model="gpt-6.1-sol")
        if row["status"] == "consultant_queue":
            assert row["workflow"]["category"] == "missing_capability"
            assert any(item["id"] == row["id"] for item in call("/unhandled"))
            report.update(metrics=row["workflow"]["metrics"], queue_verified=True, automatic_handoff=True)
            row = action(row, action="close", comment="Teste técnico: capacidade anual ausente registrada corretamente em Não tratados.")
            assert call("/connection?company_code=EMP001")["snapshot"]["schedules"] == report["baseline_schedules"]
            report.update(passed=True, final_status=row["status"], hcm_unchanged=True)
            persist()
            print(json.dumps({k:v for k,v in report.items() if k != "baseline_schedules"}, ensure_ascii=True))
            raise SystemExit(0)
        assert row["status"] == "awaiting_interpretation"
        assert len(row["workflow"]["options"]) == 3
        assert all(o["support"] != "supported" for o in row["workflow"]["options"]), "Frequência anual inexistente não pode ser anunciada como suportada."
        report["first_options"] = row["workflow"]["options"]
        persist()
        if args.pause_at_options:
            print(json.dumps({"case_id": row["id"], "paused_at_options": True}))
            raise SystemExit(0)
    else:
        row = call(f"/cases/{report['case_id']}")
        assert row["status"] == "awaiting_interpretation" and row["workflow"]["attempt"] in (1, 2)
    try:
        if row["workflow"]["attempt"] == 1:
            row = action(row, action="choose", choice="none", comment="Nenhuma atende. Preciso esclarecer se são no máximo ou pelo menos 15 dias e a diferença entre ano-calendário e período de 12 meses.")
        assert row["status"] == "awaiting_interpretation" and row["workflow"]["attempt"] == 2
        metrics = row["workflow"]["metrics"]
        assert len(metrics) == 4 and metrics[-1]["model"] == "gpt-6.1-sol"
        report["metrics"] = metrics
        report["second_options"] = row["workflow"]["options"]
        row = action(row, action="choose", choice="none", comment="Encaminhar ao consultor para revisar o requisito e as capacidades ausentes.")
        assert row["status"] == "consultant_queue"
        assert any(item["id"] == row["id"] for item in call("/consultant-queue"))
        report["queue_verified"] = True
        row = action(row, action="close", comment="Conclusão do teste técnico: a periodicidade anual e a antecedência máxima exigem capacidades adicionais no HCM; nenhum parâmetro deve ser inventado.")
        assert row["status"] == "consultant_closed"
        assert not any(m["case_id"] == row["id"] for m in call("/interpretation-memories"))
        assert call("/connection?company_code=EMP001")["snapshot"]["schedules"] == report["baseline_schedules"]
        report.update(passed=True, final_status=row["status"], hcm_unchanged=True)
    finally:
        row = call(f"/cases/{report['case_id']}")
        if row["status"] not in ("rejected", "consultant_closed"):
            response = client.post(f"/api/cases/{row['id']}/review", json={"revision": row["revision"], "decision": "reject", "comment": "Encerramento do teste técnico sem aplicação no HCM."})
            # A response can be lost while the server still finishes the saved run.
            # A stale revision must not obscure the original error or overwrite it.
            if response.status_code != 409:
                response.raise_for_status()
        persist()
print(json.dumps({k:v for k,v in report.items() if k not in ("first_options", "second_options", "baseline_schedules")}, ensure_ascii=True))
