"""Real model -> HR catalog -> approved application -> tests -> exact reversal."""
import argparse
import json
from pathlib import Path
from urllib.parse import quote
import httpx

parser=argparse.ArgumentParser()
parser.add_argument("--execute",action="store_true")
parser.add_argument("--company-query",default="asdfasf")
args=parser.parse_args()
if not args.execute: raise SystemExit("Use --execute para o teste reversível em homologação.")
root=Path(__file__).resolve().parents[1]
password=next(line.split(": ",1)[1] for line in (root/".local-access.txt").read_text(encoding="utf-8").splitlines() if line.startswith("Senha:"))
report={"passed":False,"module":"timekeeping","provider":"real"}
case_id=None
with httpx.Client(base_url="http://localhost:3001",headers={"Origin":"http://localhost:3001"},timeout=240) as client:
    def call(path,payload=None):
        r=client.get("/api"+path) if payload is None else client.post("/api"+path,json=payload)
        if r.status_code>=400: raise RuntimeError(f"{path}: {r.status_code}: {r.json().get('detail')}")
        return r.json()
    def action(row,endpoint,**kwargs):
        return call(f"/cases/{row['id']}/{endpoint}",{"revision":row["revision"],**kwargs})
    call("/login",{"username":"consultor","password":password})
    matches=call("/companies?query="+quote(args.company_query))["items"]
    assert len(matches)==1
    company=matches[0]
    connection="/connection?module=time&company_code="+quote(company["code"])
    baseline=call(connection)["snapshot"]
    try:
        row=call("/cases",{"request":f"Teste técnico reversível: na empresa {company['name']}, em Controle de Ponto, desabilitar a permissão de horas extras e definir o limite diário de horas extras como zero. Preservar jornada, intervalo, tolerância e todos os demais valores. Não publicar em produção.","effective_from":baseline["as_of"]})
        case_id=row["id"]
        row=action(row,"orchestrate",primary_model="gpt-6-luna",escalation_model="gpt-6.1-sol")
        assert row["status"]=="awaiting_interpretation",row["workflow"].get("reason")
        assert row["workflow"]["module"]=="time" and row["company_code"]==company["code"]
        row=action(row,"workflow",action="choose",choice=row["workflow"]["options"][0]["id"])
        assert row["status"]=="review",row["workflow"].get("reason")
        expected={k:v for k,v in baseline["effective"]["homologation"][0]["configuration"].items() if k in {f["id"] for f in row["workflow"]["catalog"]["fields"]}}
        actual={**expected}
        for change in row["proposal"]["changes"]:
            assert change["group_id"] is None and change["parameter"] in ("allow_overtime","max_overtime_hours")
            actual[change["parameter"]]=change["value"]
        assert actual["allow_overtime"] is False and actual["max_overtime_hours"]==0
        report["metrics"]=row["workflow"]["metrics"]
        row=action(row,"execute",approval_hash=row["approval_hash"])
        assert row["status"]=="applied" and row["result"]["automatic_tests"]["passed"]
        assert row["result"]["configuration_matches"] and row["result"]["production_unchanged"]
        report["checks"]=row["result"]["automatic_tests"]["checks"]
        row=action(row,"verify")
        row=action(row,"confirm-result",comment="Validação técnica da integração real de Controle de Ponto; reverter imediatamente.")
        assert row["status"]=="validated"
        row=action(row,"rollback",comment="Reversão do teste técnico de Controle de Ponto; restaurar exatamente a regra anterior.")
        assert row["status"]=="reverted"
        assert call(connection)["snapshot"]["schedules"]==baseline["schedules"]
        report.update(passed=True,case_id=case_id,company=company["name"],company_code=company["code"],restored_exact_baseline=True,production_unchanged=True)
    finally:
        if case_id is not None:
            row=call(f"/cases/{case_id}")
            if row["status"] in ("applied","validated","verification_failed","rollback_unknown"):
                action(row,"rollback",comment="Limpeza obrigatória do teste técnico de Controle de Ponto.")
            elif row["status"] not in ("reverted","rejected","consultant_closed"):
                action(row,"review",decision="reject",comment="Teste técnico encerrado sem aprovação de solução de negócio.")
            for memory in call("/interpretation-memories"):
                if memory["case_id"]==case_id and memory["active"]:call(f"/interpretation-memories/{memory['id']}/forget",{})
        (root/"artifacts").mkdir(exist_ok=True)
        (root/"artifacts"/"live-hr-acceptance.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps({k:v for k,v in report.items() if k not in ("metrics","checks")},ensure_ascii=True))
