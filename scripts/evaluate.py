"""Functional evaluation harness: runs the parameter-level scenarios of evaluation/scenarios.json against the
real model, on a synthetic HCM state built per scenario, and judges the proposal against the expected outcome.

Run inside the backend image so the provider configuration and dependencies are available:
  docker compose run --rm --no-deps -v "C:/projects/ERP-AI:/repo" backend python /repo/scripts/evaluate.py --execute

Without --execute it only lists which scenarios are evaluable. Nothing touches the HCM: the state is synthetic
and the official validator is replaced by the same dependency rules the fake HCM of the test suite applies.
Costs one model call per scenario. The report goes to artifacts/evaluation-<timestamp>.json.
"""
import argparse
import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

parser = argparse.ArgumentParser()
parser.add_argument("--execute", action="store_true", help="call the real model (one generation per scenario)")
parser.add_argument("--scenarios", default=str(ROOT / "evaluation" / "scenarios.json"))
parser.add_argument("--only", default="", help="comma-separated scenario ids")
parser.add_argument("--model", default="", help="override the configured model id")
args = parser.parse_args()

from app import provider  # noqa: E402
from app.contracts import Proposal  # noqa: E402
from app.workflow import checked_payloads  # noqa: E402
from test_workflow import CATALOG, FIELDS, FakeHCM, base_data, resolve  # noqa: E402

document = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
wanted = {item.strip() for item in args.only.split(",") if item.strip()}
scenarios = [s for s in document["scenarios"] if s.get("kind") == "parameter" and (not wanted or s["id"] in wanted)]
skipped = [s["id"] for s in document["scenarios"] if s.get("kind") != "parameter"]
if not args.execute:
    print(json.dumps({"evaluable": [s["id"] for s in scenarios], "flow_or_manual": skipped, "hint": "use --execute para rodar com o modelo real"}, ensure_ascii=False))
    raise SystemExit(0)

config = provider.read_config()
if not config.get("api_key"):
    raise SystemExit("Configure o modelo antes da avaliação")
if args.model:
    config = {**config, "model": args.model}

def build_state(setup):
    data = base_data()
    company_rule = data["schedules"]["homologation"][0]
    company_rule.update(setup.get("company", {}))
    data["schedules"]["production"][0].update(setup.get("company", {}))
    next_id = 10
    for group in setup.get("groups", []):
        target = next((g for g in data["groups"] if g["name"] == group["name"]), None)
        if target is None:
            next_id += 1
            target = {"id": next_id, "name": group["name"], "employee_count": 1, "primary_count": 1, "member_count": 1, "overlap_count": 0}
            data["groups"].append(target)
        if group.get("rule"):
            data["schedules"]["homologation"].append({**company_rule, "id": 100 + target["id"], "scope_key": f"company:1:group:{target['id']}", "group_id": target["id"], "version": 1, **group["rule"]})
    if setup.get("future"):
        data["schedules"]["homologation"].append({**company_rule, "id": 50, "version": 2, "effective_from": setup["future"]["effective_from"], **setup["future"]["fields"]})
    for index, named in enumerate(setup.get("named", []), 1):
        data["schedules"]["homologation"].append({**company_rule, "id": 200 + index, "scope_key": f"company:1:config:{named['code']}", "group_id": None, "version": 1,
                                                  "config_code": named["code"], "name": named["name"], "simulation_text": None, **named.get("fields", {})})
    if setup.get("assignments") is not None:
        data["assignments"]["homologation"] = {"vacations": setup["assignments"], "vacations_simulation": [], "version": 1, "effective_from": data["as_of"]}
    return data

def scope_of(change):
    if change.config_code:
        return "config:" + change.config_code
    return "company" if change.group_id is None else "group:" + GROUP_NAMES.get(change.group_id, str(change.group_id))

def effective_changes(proposal, payloads, snapshot):
    """What really changes in the HCM: payload fields that differ from the rule the scope inherits or already has.
    Restating inherited values when a new group rule or named configuration is created is not a change."""
    if payloads is None:
        return {(scope_of(c), c.parameter, json.dumps(c.value)) for c in proposal.changes}
    general = snapshot["effective"]["homologation"][0]["configuration"]
    by_group = {row["group_id"]: row for row in snapshot["effective"]["homologation"]}
    named = {item["config_code"]: item for item in snapshot["named_configurations"]["homologation"]}
    found = set()
    for payload in payloads:
        if payload.get("config_code"):
            scope, base = "config:" + payload["config_code"], named.get(payload["config_code"]) or general
        elif payload.get("group_id") is not None:
            row = by_group.get(payload["group_id"], {})
            scope, base = "group:" + GROUP_NAMES.get(payload["group_id"], str(payload["group_id"])), row.get("configuration") or general
        else:
            scope, base = "company", general
        found |= {(scope, field, json.dumps(payload[field])) for field in FIELDS if payload[field] != base[field]}
    return found

def judge(scenario, proposal, payload_error, found):
    expect = scenario["expect"]
    reasons = []
    if expect.get("questions"):
        if not proposal.questions:
            reasons.append("esperava esclarecimento, veio proposta")
        if proposal.changes:
            reasons.append("propôs alterações num pedido impossível ou ambíguo")
    else:
        if proposal.questions:
            reasons.append("perguntou em vez de propor")
        wanted = {(c["scope"], c["parameter"], json.dumps(c["value"])) for c in expect.get("changes", [])}
        if expect.get("no_changes"):
            wanted = set()
        if found - wanted:
            reasons.append("alterações indevidas: " + ", ".join(sorted(f"{s}/{p}={v}" for s, p, v in found - wanted)))
        if wanted - found:
            reasons.append("alterações faltantes: " + ", ".join(sorted(f"{s}/{p}={v}" for s, p, v in wanted - found)))
        if "assignments" in expect:
            got = [a.model_dump() for a in proposal.assignments.vacations] if proposal.assignments else None
            if got != expect["assignments"]:
                reasons.append(f"atribuições esperadas {expect['assignments']}, obtidas {got}")
        for code in expect.get("named", []):
            if code not in {n.config_code for n in proposal.named_configurations}:
                reasons.append(f"configuração nomeada {code} não declarada")
        if payload_error and not expect.get("no_changes"):
            reasons.append("validador rejeitou: " + payload_error)
    return not reasons, reasons

results = []
for scenario in scenarios:
    fake = FakeHCM()
    fake.data = build_state(scenario.get("setup", {}))
    GROUP_NAMES = {g["id"]: g["name"] for g in fake.data["groups"]}
    snapshot = fake.snapshot(fake.data["as_of"], "EMP001")
    sources = [{"id": "catalog:" + CATALOG["version"], "title": "Catálogo oficial de Férias do HCM", "content": copy.deepcopy(CATALOG)}]
    started = time.monotonic()
    record = {"id": scenario["id"], "family": scenario["family"], "request": scenario["request"], "expected": scenario["expect"]}
    try:
        proposal, metrics = provider.generate(scenario["request"], snapshot, sources, [], config=config)
        payload_error, payloads = None, []
        if proposal.changes or proposal.assignments is not None or proposal.named_configurations:
            try:
                payloads, _ = checked_payloads(proposal, snapshot, CATALOG, fake)
            except Exception as exc:  # the deterministic layer is part of what is evaluated
                detail = getattr(exc, "detail", str(exc))
                payload_error = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)
                payloads = None
        passed, reasons = judge(scenario, proposal, payload_error, effective_changes(proposal, payloads, snapshot))
        record.update(passed=passed, reasons=reasons, questions=proposal.questions, summary=proposal.summary,
                      changes=[{"scope": scope_of(c), "parameter": c.parameter, "value": c.value} for c in proposal.changes],
                      assignments=proposal.assignments.model_dump() if proposal.assignments else None,
                      named=[n.config_code for n in proposal.named_configurations], validator_error=payload_error,
                      model=metrics.get("model"), prompt_version=metrics.get("prompt_version"), usage=metrics.get("usage"), cost=metrics.get("cost"), seconds=round(time.monotonic() - started, 2))
    except Exception as exc:
        detail = getattr(exc, "detail", str(exc))
        record.update(passed=False, reasons=["erro: " + (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))], seconds=round(time.monotonic() - started, 2))
    results.append(record)
    print(f"{record['id']} {'PASS' if record['passed'] else 'FAIL'} {'; '.join(record.get('reasons', []))}")

total_cost = sum(float((r.get("cost") or {}).get("estimated_usd") or 0) for r in results)
summary = {"executed_at": datetime.now(timezone.utc).isoformat(), "model": config["model"], "prompt_version": provider.PROMPT_VERSION, "catalog_version": CATALOG["version"],
           "scenarios": len(results), "passed": sum(1 for r in results if r["passed"]), "failed": [r["id"] for r in results if not r["passed"]],
           "by_family": {}, "estimated_cost_usd": round(total_cost, 6), "flow_or_manual": skipped,
           "basis": "Estado sintético por cenário; validador substituído pelas regras de dependência do HCM falso da suíte. Não substitui a revisão do especialista."}
for record in results:
    family = summary["by_family"].setdefault(record["family"], {"passed": 0, "total": 0})
    family["total"] += 1
    family["passed"] += record["passed"]
(ROOT / "artifacts").mkdir(exist_ok=True)
path = ROOT / "artifacts" / ("evaluation-" + datetime.now().strftime("%Y%m%d-%H%M") + ".json")
path.write_text(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k != "basis"}, ensure_ascii=False))
print("relatório:", path)
