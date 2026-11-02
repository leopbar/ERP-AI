"""A request that spans modules becomes one case with one plan per module: the interpreter brings the other
module in, the planner tags each change with its module, every module is validated with its own catalog, and the
execution applies one HCM operation per module, undoing the earlier ones when a later one fails."""
import copy
import hashlib
import json
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_workflow import setup, FakeHCM, digest, PROPOSAL  # noqa: F401
from test_orchestration import agents, post, start, options, OPTIONS  # noqa: F401
from test_execution_flow import Executor
from app import orchestration as flow
from app.contracts import Proposal
from app.hcm import get_hcm
from app.main import app
from app.models import Case
from app.workflow import plans_approval_hash, approval_hash

ASSIGN_RULE = {"process": "*", "requester_group": None, "targets": ["self", "direct"], "config_source": "target"}
ASSIGN_CATALOG = {"version": "assignments-1", "contract": 2, "module": "assignments", "module_label": "Atribuições de seleção", "erp_version": "0.2.0",
                  "contract_hash": hashlib.sha256(b"assignments-1-test-contract").hexdigest(), "scopes": [{"type": "company", "id_field": None}],
                  "precedence": [{"level": 1, "scope": "company"}], "rules": ["Ao menos uma regra; targets entre self, direct, indirect e visibility."],
                  "fields": [{"id": "rules", "label": "Regras de seleção", "type": "array", "schema": {"type": "array", "minItems": 1, "items": {
                      "type": "object", "additionalProperties": False, "required": ["process", "requester_group", "targets", "config_source"],
                      "properties": {"process": {"type": "string", "enum": ["*", "vacations"]}, "requester_group": {"type": ["string", "null"]},
                                     "targets": {"type": "array", "minItems": 1, "items": {"type": "string", "enum": ["self", "direct", "indirect", "visibility"]}},
                                     "config_source": {"type": "string", "enum": ["requester", "target"]}}}}}]}
NEW_RULES = [{"process": "*", "requester_group": "Operacional", "targets": ["direct"], "config_source": "target"}, ASSIGN_RULE]

class ModuleFake:
    """A company-wide module of the fake HCM, like assignments: one rule row per environment, its own operations."""
    def __init__(self, parent, module, catalog, fields):
        self.parent, self.module, self.catalog_data = parent, module, catalog
        row = {"id": 1, "scope_key": "company:1:default", "company_id": 1, "group_id": None, "version": 1, "effective_from": "2026-01-01", "active": True,
               "config_code": None, "name": None, "simulation_text": None, **fields}
        self.data = {"schedules": {environment: [{**row, "environment": environment}] for environment in ("homologation", "production")}}
        self.operations, self.states, self.fail_next = {}, {}, False
    def for_module(self, module): return self.parent.for_module(module)
    def modules(self): return self.parent.modules()
    def module_parameters(self, module): return self.parent.module_parameters(module)
    def companies(self, query=""): return self.parent.companies(query)
    def catalog(self): return copy.deepcopy(self.catalog_data)
    def snapshot(self, as_of, company_code):
        effective = {}
        for environment, rows in self.data["schedules"].items():
            latest = max((row for row in rows if row["effective_from"] <= str(as_of)), key=lambda row: (row["effective_from"], row["version"]))
            effective[environment] = [{"group_id": None, "group_name": "Regra geral", "configuration": latest, "origin": {"scope": "company", "version": latest["version"]}}]
        snapshot = {"contract": 2, "module": self.module, "company": self.parent.data["company"], "as_of": str(as_of), "consulted_at": "2026-10-31T10:00:00+00:00",
                    "catalog_version": self.catalog_data["version"], "contract_hash": self.catalog_data["contract_hash"], "erp_version": "0.2.0", "groups": [],
                    "schedules": copy.deepcopy(self.data["schedules"]), "effective": effective, "assignments": None}
        snapshot["fingerprint"] = digest({key: snapshot[key] for key in ("schedules", "as_of", "catalog_version", "company")})
        return snapshot
    def validate(self, snapshot, payloads, assignments=None):
        return {"valid": True, "errors": [], "persisted": False}
    def apply(self, packet):
        if packet["operation_id"] in self.operations:
            return self.operations[packet["operation_id"]]
        if self.fail_next:
            self.fail_next = False
            raise HTTPException(422, "Regra recusada pelo validador do módulo")
        before = self.snapshot(packet["effective_from"], packet["company_code"])
        assert before["fingerprint"] == packet["expected_fingerprint"]
        if packet["approval_hash"] != approval_hash(before, packet["configurations"], packet.get("assignments")):
            raise HTTPException(409, "O plano não corresponde à aprovação recebida")
        self.states[packet["operation_id"]] = copy.deepcopy(self.data)
        rows = self.data["schedules"]["homologation"]
        for item in packet["configurations"]:
            rows.append({"id": len(rows) + 1, "scope_key": "company:1:default", "environment": "homologation", "version": max(r["version"] for r in rows) + 1, "active": True, **item})
        after = self.snapshot(packet["effective_from"], packet["company_code"])
        operation = {"operation_id": packet["operation_id"], "status": "applied", "approved_by": packet["approved_by"], "effective_from": packet["effective_from"],
                     "before_snapshot": before, "after_snapshot": after, "approval_hash": packet["approval_hash"],
                     "report": {"passed": True, "checks": [{"name": "Regras aplicadas", "passed": True, "origin": "HCM"}]}}
        self.operations[packet["operation_id"]] = operation
        return operation
    def verify(self, operation_id):
        operation = self.operations[operation_id]
        current = self.snapshot(operation["effective_from"], "EMP001")
        return {"status": operation["status"], "current_snapshot": current, "report": {"passed": current["schedules"]["homologation"] == operation["after_snapshot"]["schedules"]["homologation"], "checks": [{"name": "Regras correspondem ao plano", "passed": True}]}}
    def rollback(self, operation_id, expected, reason):
        operation = self.operations[operation_id]
        if self.snapshot(operation["effective_from"], "EMP001")["fingerprint"] != expected:
            raise HTTPException(409, "Mudança posterior")
        self.data = copy.deepcopy(self.states[operation_id])
        operation["status"] = "reverted"; operation["report"] = {**operation["report"], "rollback": {"passed": True, "reason": reason}}
        return operation

class MultiHCM(FakeHCM):
    def __init__(self):
        super().__init__()
        self.extra = {"assignments": ModuleFake(self, "assignments", ASSIGN_CATALOG, {"rules": [ASSIGN_RULE]})}
    def for_module(self, module):
        return self.extra.get(module, self)
    def modules(self):
        return [*super().modules(), {"id": "assignments", "label": "Atribuições de seleção", "description": "Para quem cada processo pode ser aberto: próprio solicitante, subordinados ou por visibilidade.", "scopes": ["company"]}]
    def module_parameters(self, module):
        return ["Regras de seleção"] if module == "assignments" else super().module_parameters(module)

REQUEST = "Na ABC, funcionários do grupo Operacional não podem pedir as próprias férias; apenas seus gestores e o RH pedem por eles."
MULTI_PROPOSAL = {"summary": "Bloqueia a autossolicitação do Operacional em Férias e restringe a seleção em Atribuições. O perfil de RH já pode solicitar.", "questions": [],
                  "changes": [{"module": "vacations", "group_id": 10, "parameter": "employee_request_enabled", "value": False, "reason": "Operacional não pede as próprias férias"},
                              {"module": "assignments", "parameter": "rules", "value": NEW_RULES, "reason": "Operacional só abre pedidos para subordinados diretos"}],
                  "tests": ["Operacional não consegue abrir o próprio pedido", "Gestor direto abre pedido para o Operacional", "Produção preservada"],
                  "sources": ["catalog:vacations-2", "catalog:assignments:assignments-1"]}

def multi_agents(agents, monkeypatch, second_round_supported=True, handoff=None, mention_only=False):
    """Interpreter: first round in Férias asks for Atribuições; second round, with both catalogs, finds a supported option."""
    client, fake, factory, calls = agents
    multi = MultiHCM()
    multi.data = fake.data
    app.dependency_overrides[get_hcm] = lambda: multi
    executor = Executor(multi)
    multi.apply, multi.verify, multi.rollback = executor.apply, executor.verify, executor.rollback
    original = flow.provider.completion
    rounds = []
    def completion(config, messages, schema, *, name, **kwargs):
        value, metrics = original(config, messages, schema, name=name, **kwargs)
        if name != "interpretation_agent":
            return value, metrics
        payload = json.loads(messages[-1]["content"])
        rounds.append(payload)
        for option in value["options"]:
            option["support"] = "partial"; option["limitations"] = ["Quem pode pedir pelo Operacional fica em Atribuições de seleção."]
            option["missing_capabilities"] = [{"module": "vacations", "description": "Solicitação por gestor direto."}]
        if "assignments" not in payload["additional_catalogs"]:
            if mention_only:  # the interpreter names the other module in the target level but forgets additional_modules
                value["options"][0].update(support="supported", limitations=[], missing_capabilities=[], target_level="Regra do grupo Operacional em Férias e regra geral em Atribuições de seleção")
            else:
                value["additional_modules"] = ["assignments"]
            value["explanation"] = "Férias bloqueia o pedido próprio; quem pede pelo Operacional fica em Atribuições de seleção."
        elif second_round_supported:
            value["options"][0].update(support="supported", limitations=[], missing_capabilities=[], already_in_place=["O perfil de RH já pode solicitar férias por qualquer funcionário."],
                                       target_level="Regra do grupo Operacional em Férias e regra geral em Atribuições de seleção")
        else:
            value["handoff"] = handoff
        return value, metrics
    monkeypatch.setattr(flow.provider, "completion", completion)
    def generate(request, snapshot, sources, feedback, config=None, catalogs=None, snapshots=None):
        assert list(catalogs) == ["vacations", "assignments"] and snapshots["assignments"]["module"] == "assignments"
        assert any("Informe module em cada alteração" in line for line in feedback)
        return Proposal.model_validate(MULTI_PROPOSAL), {"mode": "model", "model": config["model"], "usage": {}, "duration_seconds": 0.1}
    monkeypatch.setattr(flow.provider, "generate", generate)
    row = client.post("/api/cases", json={"request": REQUEST, "effective_from": "2026-10-31"}).json()
    return client, multi, factory, executor, rounds, post(client, row, "orchestrate")

def test_request_across_two_modules_becomes_one_plan_applied_module_by_module(agents, monkeypatch):
    client, multi, factory, executor, rounds, row = multi_agents(agents, monkeypatch)
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("reason")
    assert len(rounds) == 2 and rounds[1]["modules_in_scope"] == ["Férias", "Atribuições de seleção"]
    assert rounds[1]["additional_catalogs"]["assignments"]["module"] == "assignments" and "capabilities" not in rounds[1]["additional_catalogs"]["assignments"]
    assert row["workflow"]["modules_in_scope"] == ["vacations", "assignments"] and row["workflow"]["module_label"] == "Férias + Atribuições de seleção"
    assert row["module"] == "vacations" and row["workflow"]["extensions"] == ["assignments"]
    assert any(e["type"] == "module_extended" and e["modules"] == ["assignments"] for e in row["events"])
    assert row["workflow"]["options"][0]["already_in_place"] == ["O perfil de RH já pode solicitar férias por qualquer funcionário."]
    row = post(client, row, action="choose", choice="1")
    assert row["status"] == "review", row["workflow"].get("reason")
    plans = row["proposal"]["plans"]
    assert [plan["module"] for plan in plans] == ["vacations", "assignments"] and [plan["label"] for plan in plans] == ["Férias", "Atribuições de seleção"]
    assert plans[0]["payloads"][0]["group_id"] == 10 and plans[0]["payloads"][0]["employee_request_enabled"] is False
    assert plans[1]["payloads"][0]["rules"] == NEW_RULES and plans[1]["snapshot"]["module"] == "assignments"
    assert row["proposal"]["payloads"] == plans[0]["payloads"]
    assert row["approval_hash"] == plans_approval_hash(plans) and row["approval_hash"] != plans_approval_hash(plans[:1])
    before_assign = copy.deepcopy(multi.extra["assignments"].data)
    # Any module out of date blocks the whole approval.
    multi.extra["assignments"].data["schedules"]["homologation"][0]["version"] += 1
    response = client.post(f"/api/cases/{row['id']}/execute", json={"revision": row["revision"], "approval_hash": row["approval_hash"]})
    assert response.status_code == 409 and response.json()["detail"].startswith("Atribuições de seleção:")
    multi.extra["assignments"].data = before_assign
    row = post(client, row, "execute", approval_hash=row["approval_hash"])
    assert row["status"] == "applied" and row["result"]["automatic_tests"]["passed"]
    assert [op["label"] for op in row["result"]["operations"]] == ["Férias", "Atribuições de seleção"]
    assert row["result"]["configuration_matches"] and row["result"]["production_unchanged"]
    assert len(multi.data["schedules"]["homologation"]) == 2 and multi.extra["assignments"].data["schedules"]["homologation"][-1]["rules"] == NEW_RULES
    assert [c["name"] for c in row["result"]["automatic_tests"]["checks"]][0].startswith("Férias: ")
    row = post(client, row, "verify")
    assert row["status"] == "applied" and len(row["result"]["operations"]) == 2
    row = post(client, row, "confirm-result")
    assert row["status"] == "validated"
    with factory() as db:
        case = db.scalar(select(Case).where(Case.id == row["id"]))
        assert case.module == "vacations" and case.proposal["changes"][1]["module"] == "assignments"
    row = post(client, row, "rollback")
    assert row["status"] == "reverted" and all(op["rolled_back"] for op in row["result"]["operations"])
    assert len(multi.data["schedules"]["homologation"]) == 1 and multi.extra["assignments"].data["schedules"]["homologation"][-1]["rules"] == [ASSIGN_RULE]

def test_a_module_named_in_a_supported_option_is_brought_into_the_case(agents, monkeypatch):
    client, multi, factory, executor, rounds, row = multi_agents(agents, monkeypatch, mention_only=True)
    assert row["status"] == "awaiting_interpretation" and len(rounds) == 2
    assert row["workflow"]["modules_in_scope"] == ["vacations", "assignments"]

def test_failure_in_the_second_module_undoes_the_first(agents, monkeypatch):
    client, multi, factory, executor, rounds, row = multi_agents(agents, monkeypatch)
    row = post(client, row, action="choose", choice="1")
    before = copy.deepcopy(multi.data)
    multi.extra["assignments"].fail_next = True
    response = client.post(f"/api/cases/{row['id']}/execute", json={"revision": row["revision"], "approval_hash": row["approval_hash"]})
    assert response.status_code == 422 and response.json()["detail"].startswith("Atribuições de seleção:") and "desfeitas" in response.json()["detail"]
    row = client.get(f"/api/cases/{row['id']}").json()
    assert row["status"] == "execution_failed"
    assert multi.data == before  # the vacations change did not stay
    assert [op["label"] for op in row["result"]["operations"]] == ["Férias"] and row["result"]["operations"][0]["rolled_back"]
    kinds = [e["type"] for e in row["events"]]
    assert "execution_compensated" in kinds and "execution_failed" in kinds
    assert row["id"] in {item["id"] for item in client.get("/api/unhandled").json()}

def test_nothing_possible_gives_a_four_line_reason(agents, monkeypatch):
    handoff = {"understood": "Você quer que o Operacional não peça as próprias férias e que só gestores e RH peçam por eles.",
               "possible": "Férias já permite bloquear o pedido próprio e o perfil de RH já pode pedir.",
               "blocked_by": "Não existe um grupo de RH cadastrado na ABC para limitar quem mais pode pedir.",
               "next_step": "Peça só o bloqueio do pedido próprio, ou cadastre o grupo de RH no HCM e repita o pedido."}
    client, multi, factory, executor, rounds, row = multi_agents(agents, monkeypatch, second_round_supported=False, handoff=handoff)
    assert row["status"] == "consultant_queue" and row["workflow"]["category"] == "missing_capability"
    assert row["workflow"]["handoff_summary"] == handoff
    lines = row["workflow"]["reason"].split("\n")
    assert len(lines) == 4 and lines[0].startswith("O que entendi: ") and lines[3].startswith("O que fazer agora: ")
    assert row["workflow"]["module_label"] == "Férias + Atribuições de seleção"
    assert client.get("/api/unhandled").json()[0]["reason"] == row["workflow"]["reason"]

def test_single_module_cases_keep_their_shape(agents):
    client, fake, factory, calls = agents
    row = post(client, options(client, start(client)), action="choose", choice="1")
    assert row["status"] == "review" and [plan["module"] for plan in row["proposal"]["plans"]] == ["vacations"]
    assert row["proposal"]["payloads"][0]["max_installments"] == 3 and row["workflow"]["modules_in_scope"] == ["vacations"]
    assert row["workflow"]["module_label"] == "Férias"
