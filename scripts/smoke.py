"""Live smoke test: only writes a rejected technical case in ERP AI; never HCM."""
from pathlib import Path
import httpx

root = Path(__file__).resolve().parents[1]
access = (root / ".local-access.txt").read_text(encoding="utf-8").splitlines()
password = next(line.split(": ", 1)[1] for line in access if line.startswith("Senha:"))
with httpx.Client(base_url="http://localhost:3001", headers={"Origin": "http://localhost:3001"}, timeout=30) as client:
    def call(path, payload=None):
        response = client.post("/api" + path, json=payload) if payload is not None else client.get("/api" + path)
        response.raise_for_status()
        return response.json()
    call("/login", {"username": "consultor", "password": password})
    profile = call("/me")
    assert profile["username"] == "consultor"
    assert not profile["model_configured"], "Smoke requires an unconfigured provider; no model calls will be made."
    connection = call("/connection?company_code=EMP001")
    baseline = connection["snapshot"]["schedules"]
    assert connection["company"]["code"] == "EMP001"
    case = call("/cases", {"request": "Teste técnico de integração — conferir consulta e preservar todas as configurações.", "effective_from": connection["snapshot"]["as_of"], "company_code": connection["snapshot"]["company"]["code"]})
    case = call(f"/cases/{case['id']}/analyze", {"revision": case["revision"]})
    assert case["status"] == "awaiting_model", "Smoke requires provider to be unconfigured; do not spend tokens."
    proposal = {"summary": "Teste técnico sem alterações; não representa um caso funcional aprovado.", "questions": [], "changes": [], "tests": ["Confirmar que nenhuma configuração foi alterada."], "sources": ["catalog:" + connection["catalog"]["version"]]}
    case = call(f"/cases/{case['id']}/manual-proposal", {"revision": case["revision"], "proposal": proposal})
    case = call(f"/cases/{case['id']}/review", {"revision": case["revision"], "decision": "accept"})
    case = call(f"/cases/{case['id']}/check-result", {"revision": case["revision"]})
    assert case["result"]["configuration_matches"] and case["result"]["production_unchanged"]
    case = call(f"/cases/{case['id']}/review", {"revision": case["revision"], "decision": "reject", "comment": "Teste técnico concluído. Não usar como experiência funcional aprovada."})
    assert case["status"] == "rejected"
    assert call("/connection?company_code=EMP001")["snapshot"]["schedules"] == baseline
    call("/logout", {})
    assert client.get("/api/cases").status_code == 401
    print(f"Live smoke OK: login, proxy, PostgreSQL, HCM, proposal/review/result; technical case #{case['id']} rejected; HCM unchanged.")
