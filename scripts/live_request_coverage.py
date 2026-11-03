"""Live suggestion battery: requests with one or several items through the real agents, up to the plan.

Real model and real HCM reads (modules, catalogs, snapshots, companies, official validator); no HCM writes, because
execute is never called. The ERP side runs in a throwaway in-memory database with a technical login generated here,
so the consultant's cases, intent memories and lessons are untouched and nothing learned here leaks into them.
Run inside the backend image, one process per group of scenarios if you want them in parallel:
  docker compose run --rm --no-deps -T -e DATABASE_URL=sqlite:// -e LANGGRAPH_SQLITE_PATH=/tmp/live-checkpoints.db -v "C:/projects/ERP-AI:/repo" backend python /repo/scripts/live_request_coverage.py S1,S2
Report: artifacts/request-coverage-<ids>[-<tag>].json (an optional second argument tags repeated runs). Costs a few model calls per scenario.
"""
import copy
import json
import os
import secrets
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
if os.getenv("DATABASE_URL", "").startswith("postgresql"):
    sys.exit("Use DATABASE_URL=sqlite:// para isolar a bateria do banco do ERP.")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from app import orchestration as flow, provider  # noqa: E402
from app.auth import password_hash  # noqa: E402
from app.database import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

CASE_103 = ("Quero fazer alguns ajustes em férias e nas etapas de aprovação:\n"
            "1. Com os horistas é diferente. Quando um assistente tira a segunda ou a terceira parte das férias no mesmo mês, eu quero que o sistema mande isso para a folha mensal dele, e não para a complementar. Para os mensalistas, deixe como está.\n\n"
            "2. Trinta dias de aviso está muito em cima para a gente se organizar. Eu quero que o sistema só aceite férias pedidas com pelo menos 45 dias de antecedência. E quero que a aprovação aconteça no máximo até 15 dias antes de a pessoa sair. Depois disso, não dá mais tempo de pagar.\n\n"
            "3. Lembra que o meu lançamento foi aprovado sozinho? Não quero mais isso. Eu quero que todo lançamento de valores passe primeiro pelo gestor imediato da pessoa, que tem 8 horas para responder, e depois pelo RH, com 16 horas.\n\n"
            "4. Quando alguém for promovido ou mudar de área, eu quero saber o motivo. Quero que o sistema obrigue a escolher entre: Promoção, Mérito, Enquadramento ou Transferência.\n\nEmpresa ABC Tecnologia.")

def text_of(reading):
    return " ".join([reading.get("label", ""), reading.get("how", "")])

def gaveta_mensalistas_horario(items):
    """Case 103: the assistants are in Mensalistas + Horário. A user who knows it picks that reading of the drawer item;
    every other item keeps the suggested reading."""
    for item in items:
        for index, reading in enumerate(item["readings"]):
            text = text_of(reading)
            if len(item["readings"]) > 1 and "Mensalistas" in text and "Horário" in text and "Horistas + Horário" not in text and "Horistas/Horário" not in text:
                return {item["id"]: index}
    return {}

def drawer_offered(analysis):
    """The drawer item of case 103 offers the Mensalistas + Horário drawer as one of its readings."""
    items = ((analysis.get("workflow") or {}).get("suggestion") or {}).get("items") or []
    return bool(gaveta_mensalistas_horario(items))

# expect: status after the interpretation, minimum number of items, modules that must be in the case, (module, parameter)
# pairs the plan must have or must not have, and whether the HCM must be said not to publish something the choice needs.
SCENARIOS = [
    {"id": "S1", "title": "Pedido do atendimento #103", "request": CASE_103, "pick": gaveta_mensalistas_horario,
     # Homologation already has the notice and approval deadlines case 103 applied: they must come back as already in force.
     "expect": {"status": "awaiting_interpretation", "min_items": 4, "modules": ["vacation_policy", "workflow", "process_fields"], "information_gap": True,
                "has": [("vacation_policy", "general_configs"), ("workflow", "processes"), ("process_fields", "movement")], "in_place": 1,
                "not": [("vacations", "min_advance_days")], "check": ("gaveta como dúvida", drawer_offered)}},
    {"id": "S2", "title": "Um item só", "request": "Na ABC Tecnologia, quero permitir que as férias sejam divididas em até três parcelas.",
     "expect": {"status": "awaiting_interpretation", "min_items": 1, "modules": ["vacations"], "has": [("vacations", "max_installments")]}},
    {"id": "S3", "title": "Dois itens em dois módulos", "request": "Na ABC Tecnologia, quero desabilitar as horas extras no controle de ponto e passar a exigir aprovação nas admissões.",
     "expect": {"status": "awaiting_interpretation", "min_items": 2, "modules": ["timekeeping", "admission"], "has": [("timekeeping", "allow_overtime")], "in_place": 1}},
    {"id": "S4", "title": "Itens já em vigor e um a mudar", "request": "Na ABC Tecnologia, a aprovação das férias pelo RH deve continuar obrigatória e o saldo de férias deve continuar visível para o funcionário. Além disso, quero permitir o adiantamento do 13º junto com as férias.",
     "expect": {"status": "awaiting_interpretation", "min_items": 3, "modules": ["vacations"], "has": [("vacations", "allow_13th_advance")], "in_place": 2}},
    # The user's decision: the possible part is planned with their acceptance, the impossible one is left out and registered.
    {"id": "S5", "title": "Um item impossível", "request": "Na ABC Tecnologia, quero permitir férias em até duas parcelas e limitar cada funcionário a uma única solicitação de férias por ano.",
     "expect": {"status": "awaiting_interpretation", "min_items": 2, "modules": ["vacations"], "has": [("vacations", "max_installments")], "left_out": 1}},
    {"id": "S6", "title": "Um item com dois parâmetros possíveis", "request": "Na ABC Tecnologia, quero que as férias só sejam aceitas quando pedidas com pelo menos 50 dias de antecedência.",
     "expect": {"status": "awaiting_interpretation", "min_items": 1, "modules": ["vacations"]}},
    {"id": "S7", "title": "Item 3 do #103 sozinho", "request": "Na ABC Tecnologia, meu lançamento de valores foi aprovado sozinho e não quero mais isso. Todo lançamento de valores deve passar primeiro pelo gestor imediato, com 8 horas para responder, e depois pelo RH, com 16 horas.",
     "expect": {"status": "awaiting_interpretation", "min_items": 1, "modules": ["workflow"], "has": [("workflow", "processes")]}},
    {"id": "S8", "title": "Item 4 do #103 sozinho", "request": "Na ABC Tecnologia, quando alguém for promovido ou mudar de área, o sistema deve obrigar a escolher o motivo entre Promoção, Mérito, Enquadramento ou Transferência.",
     "expect": {"status": "awaiting_interpretation", "min_items": 1, "modules": ["process_fields"], "has": [("process_fields", "movement")]}},
    {"id": "S9", "title": "Pessoas que o HCM não liga a um grupo", "request": "Na ABC Tecnologia, os coordenadores não podem parcelar as férias.",
     "expect": {"min_items": 1, "modules": ["vacations"], "information_gap": True}},
    {"id": "S10", "title": "Cinco itens em cinco módulos", "request": "Na ABC Tecnologia: 1) o desconto do vale-transporte deve ser de 6% do salário; 2) todo dependente precisa ter CPF; 3) o próprio colaborador pode solicitar benefícios; 4) afastamentos podem ser lançados com até 5 dias retroativos; 5) admissões não podem cair em fim de semana.",
     # Four of the five are already in force in homologation; only the retroactive limit of leaves changes.
     "expect": {"status": "awaiting_interpretation", "min_items": 5, "modules": ["transport", "dependents", "benefits", "leave", "admission"],
                "has": [("leave", "max_retroactive_days")], "in_place": 3}},
]

def checks(scenario, analysis, chosen, plan):
    """Structural checks; whether each reading is right is judged by a person reading the report."""
    expect, w = scenario["expect"], analysis.get("workflow") or {}
    items, modules = w.get("requirements") or [], w.get("modules_in_scope") or []
    changes = {(c.get("module") or analysis.get("module"), c["parameter"]) for c in (plan or {}).get("proposal", {}).get("changes", [])}
    found = {"status": not expect.get("status") or analysis.get("status") == expect["status"],
             "itens": len(items) >= expect.get("min_items", 1),
             "módulos": set(expect.get("modules", [])) <= set(modules)}
    if expect.get("check"):
        name, test = expect["check"]
        found[name] = test(analysis)
    if expect.get("information_gap"):
        found["lacuna registrada"] = bool(w.get("information_gaps")) or analysis.get("status") == "consultant_queue"
    if plan is not None:
        found["plano"] = plan.get("status") == "review"
        if expect.get("has"):
            found["alterações esperadas"] = set(map(tuple, expect["has"])) <= changes
        if expect.get("not"):
            found["nada fora do pedido"] = not (set(map(tuple, expect["not"])) & changes)
        if expect.get("in_place"):
            found["já em vigor"] = sum(1 for r in plan.get("proposal", {}).get("requirements", []) if r["status"] == "already_in_place") >= expect["in_place"]
        if expect.get("left_out"):
            found["fora do plano"] = len(plan.get("proposal", {}).get("not_configured") or []) >= expect["left_out"]
    return found

def summary(row):
    w = row.get("workflow") or {}
    return {"status": row.get("status"), "module": row.get("module"), "modules_in_scope": w.get("modules_in_scope"), "extensions": w.get("extensions"),
            "redirects": w.get("redirects"), "requirements": w.get("requirements"), "information_gaps": w.get("information_gaps"),
            "suggestion": w.get("suggestion"), "selected": {key: (w.get("selected") or {}).get(key) for key in ("support", "readings", "limitations", "not_configured")},
            "handoff_summary": w.get("handoff_summary"), "category": w.get("category") if row.get("status") == "consultant_queue" else "",
            "error": w.get("error"), "repair_notes": w.get("repair_notes")}

def costs(row):
    metrics = (row.get("workflow") or {}).get("metrics") or []
    by_role = {}
    for m in metrics:
        role = by_role.setdefault(m.get("role", "?"), {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0, "seconds": 0.0})
        usage = m.get("usage") or {}
        role["calls"] += 1; role["prompt_tokens"] += usage.get("prompt_tokens", 0); role["completion_tokens"] += usage.get("completion_tokens", 0)
        role["usd"] += float((m.get("cost") or {}).get("estimated_usd") or 0); role["seconds"] += float(m.get("duration_seconds") or 0)
    return by_role

def main():
    wanted = {item.strip() for item in (sys.argv[1] if len(sys.argv) > 1 else "").split(",") if item.strip()}
    chosen = [s for s in SCENARIOS if not wanted or s["id"] in wanted]
    # A technical login that exists only in this process: never the consultant's password.
    password = secrets.token_urlsafe(24)
    os.environ.update(CONSULTANT_USER="bateria-cobertura", CONSULTANT_PASSWORD_HASH=password_hash(password))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    def db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db
    # A run that stops on an error loses the agents' answers and metrics; keep the refused suggestions and the billed cost here.
    rejected, billed = [], [0.0]
    original_problems, original_completion = flow.suggestion_problems, provider.completion
    def watched_problems(items, request):
        problems = original_problems(items, request)
        if problems:
            rejected.append({"items": items, "problems": problems})
        return problems
    def billed_completion(*args, **kwargs):
        content, metrics = original_completion(*args, **kwargs)
        billed[0] += float((metrics.get("cost") or {}).get("estimated_usd") or 0)
        return content, metrics
    flow.suggestion_problems, provider.completion = watched_problems, billed_completion
    results = []
    with TestClient(app) as client:
        client.headers["Origin"] = os.getenv("APP_ORIGIN", "http://localhost:3001")
        assert client.post("/api/login", json={"username": "bateria-cobertura", "password": password}).status_code == 200
        for scenario in chosen:
            started = time.monotonic()
            rejected.clear(); billed[0] = 0.0
            row = client.post("/api/cases", json={"request": scenario["request"], "effective_from": date.today().isoformat()}).json()
            response = client.post(f"/api/cases/{row['id']}/orchestrate", json={"revision": row["revision"]})
            analysis = response.json() if response.status_code == 200 else {**client.get(f"/api/cases/{row['id']}").json(), "http_error": [response.status_code, response.json().get("detail")]}
            plan, picked = None, None
            if analysis.get("status") == "awaiting_interpretation":
                items = analysis["workflow"]["suggestion"]["items"]
                picked = (scenario.get("pick") or (lambda items: {}))(items)
                reply = client.post(f"/api/cases/{row['id']}/workflow", json={"revision": analysis["revision"], "action": "choose", "choice": "accept", "readings": picked})
                plan = reply.json() if reply.status_code == 200 else {**client.get(f"/api/cases/{row['id']}").json(), "http_error": [reply.status_code, reply.json().get("detail")]}
            final = plan or analysis
            found = checks(scenario, analysis, picked, plan)
            record = {"id": scenario["id"], "title": scenario["title"], "request": scenario["request"], "seconds": round(time.monotonic() - started, 1),
                      "checks": found, "passed": all(found.values()), "analysis": summary(analysis), "http_error": analysis.get("http_error"), "picked": picked,
                      "plan": None if plan is None else {**summary(plan), "http_error": plan.get("http_error"), "proposal": {key: (plan.get("proposal") or {}).get(key) for key in ("summary", "questions", "changes", "requirements", "not_configured", "tests")},
                                                         "plan_modules": [p["module"] for p in (plan.get("proposal") or {}).get("plans", [])]},
                      "costs": costs(final), "billed_usd": round(billed[0], 6), "suggestion_rejections": copy.deepcopy(rejected)}
            results.append(record)
            spent = billed[0]
            failed = [name for name, ok in found.items() if not ok]
            print(f"{'OK ' if record['passed'] else 'XX '}{scenario['id']:4} {scenario['title'][:42]:42} {analysis.get('status')!s:24} -> {(plan or {}).get('status')!s:18} "
                  f"itens={len((analysis.get('workflow') or {}).get('requirements') or [])} fora={len(((plan or {}).get('proposal') or {}).get('not_configured') or [])} módulos={(analysis.get('workflow') or {}).get('modules_in_scope')} "
                  f"US$ {spent:.4f} {record['seconds']}s {('falhou: ' + ', '.join(failed)) if failed else ''}", flush=True)
        client.post("/api/logout", json={})
    app.dependency_overrides.clear()
    (ROOT / "artifacts").mkdir(exist_ok=True)
    tag = ("-" + sys.argv[2]) if len(sys.argv) > 2 else ""  # several runs of the same scenarios keep separate reports
    out = ROOT / "artifacts" / f"request-coverage-{'-'.join(s['id'] for s in chosen)}{tag}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    flow.suggestion_problems, provider.completion = original_problems, original_completion
    total = sum(r["billed_usd"] for r in results)
    print(f"\n{sum(r['passed'] for r in results)}/{len(results)} cenários passaram nas verificações estruturais. Custo estimado US$ {total:.4f}. Relatório: {out}")

if __name__ == "__main__":
    main()
