"""Contract v2 in the ERP: named configurations, assignments, schema-driven values, strict-mode schema
conversion and contract-hash scoping."""
import copy
import pytest
from fastapi import HTTPException
from test_workflow import setup, opened, manual, action, CATALOG, PROPOSAL
from test_execution_flow import Executor
from app.contracts import Proposal
from app.provider import proposal_schema
from app.schema_tools import from_model_value, to_model_schema, value_errors
from app.workflow import approval_hash, checked_payloads

NAMED_PLAN = {"summary": "Criar configuração nomeada para o Operacional com três parcelas e atribuí-la",
              "questions": [], "tests": ["Conferir três parcelas no Operacional", "Conferir regra geral preservada"], "sources": ["catalog:vacations-2"],
              "changes": [{"config_code": "CFG-OPER", "parameter": "max_installments", "value": 3, "reason": "Pedido do RH"}],
              "named_configurations": [{"config_code": "CFG-OPER", "name": "Operacional com três parcelas", "simulation_text": None}],
              "assignments": {"vacations": [{"group": "Operacional", "config": "CFG-OPER"}], "vacations_simulation": []}}

def connected(setup):
    client, fake, factory = setup
    executor = Executor(fake)
    fake.apply, fake.verify, fake.rollback = executor.apply, executor.verify, executor.rollback
    return client, fake, factory, executor

def test_named_configuration_with_assignment_is_planned_applied_and_reverted(setup):
    client, fake, factory, executor = connected(setup)
    row = opened(client)
    response = manual(client, row, NAMED_PLAN)
    assert response.status_code == 200, response.text
    row = response.json()
    payloads = row["proposal"]["payloads"]
    assert len(payloads) == 1 and payloads[0]["config_code"] == "CFG-OPER" and payloads[0]["name"] == "Operacional com três parcelas"
    assert payloads[0]["group_id"] is None and payloads[0]["max_installments"] == 3 and payloads[0]["min_advance_days"] == 30  # variant of the company rule
    assert row["proposal"]["assignments"] == NAMED_PLAN["assignments"]
    assert row["approval_hash"] == approval_hash(row["snapshot"], payloads, NAMED_PLAN["assignments"])
    assert row["approval_hash"] != approval_hash(row["snapshot"], payloads, None)
    row = action(client, row, "execute", approval_hash=row["approval_hash"]).json()
    assert row["status"] == "applied", row["result"]
    assert row["result"]["configuration_matches"] and row["result"]["assignments_match"] and row["result"]["production_unchanged"]
    after = row["result"]["execution"]["after_snapshot"]
    operacional = next(item for item in after["effective"]["homologation"] if item["group_name"] == "Operacional")
    assert operacional["origin"]["scope"] == "assignment" and operacional["configuration"]["config_code"] == "CFG-OPER"
    assert after["effective"]["homologation"][0]["configuration"]["max_installments"] == 2
    row = action(client, row, "verify").json()
    assert row["status"] == "applied" and row["result"]["automatic_tests"]["passed"]
    row = action(client, row, "confirm-result").json()
    assert row["status"] == "validated"
    row = action(client, row, "rollback").json()
    assert row["status"] == "reverted"
    current = fake.snapshot("2026-10-31", "EMP001")
    assert current["named_configurations"]["homologation"] == [] and current["assignments"]["homologation"]["vacations"] == []

def test_group_rule_shadowed_by_assignment_is_refused_before_the_hcm(setup):
    client, fake, _ = setup
    fake.data["schedules"]["homologation"].append({**fake.data["schedules"]["homologation"][0], "id": 5, "scope_key": "company:1:config:CFG-X", "version": 1, "config_code": "CFG-X", "name": "X", "max_installments": 3})
    fake.data["assignments"]["homologation"]["vacations"] = [{"group": "Operacional", "config": "CFG-X"}]
    row = opened(client)
    shadowed = copy.deepcopy(PROPOSAL); shadowed["changes"][0]["group_id"] = 10
    response = manual(client, row, shadowed)
    assert response.status_code == 422 and "atribuição" in response.json()["detail"]
    fixed = copy.deepcopy(PROPOSAL); fixed["changes"][0]["config_code"] = "CFG-X"; fixed["changes"][0]["value"] = 2
    response = manual(client, row, fixed)
    assert response.status_code == 200, response.text
    assert response.json()["proposal"]["payloads"][0]["config_code"] == "CFG-X" and response.json()["proposal"]["payloads"][0]["max_installments"] == 2

def test_assignments_and_named_declarations_are_validated(setup):
    client, fake, _ = setup
    row = opened(client)
    missing = copy.deepcopy(NAMED_PLAN); missing["named_configurations"] = []
    assert "named_configurations" in manual(client, row, missing).json()["detail"]
    unknown_group = copy.deepcopy(NAMED_PLAN); unknown_group["assignments"]["vacations"][0]["group"] = "Diretoria"
    assert "Diretoria" in manual(client, row, unknown_group).json()["detail"]
    unknown_config = copy.deepcopy(NAMED_PLAN); unknown_config["assignments"]["vacations"].append({"group": "Operacional", "config": "NAO-EXISTE"})
    assert "NAO-EXISTE" in manual(client, row, unknown_config).json()["detail"]
    same = copy.deepcopy(PROPOSAL); same["assignments"] = {"vacations": [], "vacations_simulation": []}
    assert "iguais" in manual(client, row, same).json()["detail"]

def test_module_without_named_scope_rejects_named_plans(setup):
    client, fake, _ = setup
    fake.catalog_overrides = {"scopes": [{"type": "company"}]}
    row = opened(client)
    assert manual(client, row, NAMED_PLAN).status_code == 422
    grouped = copy.deepcopy(PROPOSAL); grouped["changes"][0]["group_id"] = 10
    assert "Escopo" in manual(client, row, grouped).json()["detail"]
    row = manual(client, row).json()
    assert "config_code" not in row["proposal"]["payloads"][0]

WORKFLOW_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["steps", "group_overrides"], "properties": {
    "steps": {"type": "array", "maxItems": 5, "items": {"type": "object", "additionalProperties": False, "required": ["approver", "sla_hours"],
                                                        "properties": {"approver": {"type": "string", "enum": ["hr", "direct_manager"]}, "sla_hours": {"type": "integer", "minimum": 1, "maximum": 720}, "finish": {"type": "boolean"}}}},
    "group_overrides": {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["approver", "sla_hours"],
                                                                                              "properties": {"approver": {"type": "string"}, "sla_hours": {"type": "integer"}}}}}}}

def strict_ok(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, schema
            assert set(schema.get("required", [])) == set(schema.get("properties", {})), schema
        assert not {"pattern", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "uniqueItems"} & set(schema), schema
        for value in schema.values():
            strict_ok(value)
    elif isinstance(schema, list):
        for item in schema:
            strict_ok(item)

def test_strict_schema_conversion_round_trips_maps_and_optional_fields():
    model_schema = to_model_schema(WORKFLOW_SCHEMA)
    strict_ok(model_schema)
    overrides = model_schema["properties"]["group_overrides"]
    assert overrides["type"] == "array" and set(overrides["items"]["properties"]) == {"key", "value"}
    assert model_schema["properties"]["steps"]["items"]["properties"]["finish"] == {"type": ["boolean", "null"]}
    answered = {"steps": [{"approver": "hr", "sla_hours": 16, "finish": None}, {"approver": "direct_manager", "sla_hours": 8, "finish": True}],
                "group_overrides": [{"key": "Gestores", "value": [{"approver": "hr", "sla_hours": 4}]}]}
    restored = from_model_value(answered, WORKFLOW_SCHEMA)
    assert restored == {"steps": [{"approver": "hr", "sla_hours": 16}, {"approver": "direct_manager", "sla_hours": 8, "finish": True}],
                        "group_overrides": {"Gestores": [{"approver": "hr", "sla_hours": 4}]}}
    assert value_errors(restored, WORKFLOW_SCHEMA) == []
    assert any("sla_hours" in error for error in value_errors({**restored, "steps": [{"approver": "hr", "sla_hours": 0}]}, WORKFLOW_SCHEMA))
    assert any("ceo" in error for error in value_errors({**restored, "steps": [{"approver": "ceo", "sla_hours": 1}]}, WORKFLOW_SCHEMA))

def test_proposal_schema_is_strict_compatible_and_catalog_driven(setup):
    _, fake, _ = setup
    snapshot = fake.snapshot("2026-10-31", "EMP001")
    catalog = fake.catalog()
    catalog["fields"].append({"id": "approval_flow", "label": "Fluxo", "type": "object", "schema": WORKFLOW_SCHEMA})
    schema = proposal_schema(snapshot, [{"id": "catalog:vacations-2", "content": catalog}, {"id": "case:7", "content": {}}])
    strict_ok(schema)
    variants = schema["properties"]["changes"]["items"]["anyOf"]
    assert [variant["properties"]["parameter"]["enum"][0] for variant in variants] == [field["id"] for field in catalog["fields"]]
    assert variants[0]["properties"]["group_id"] == {"type": ["integer", "null"], "enum": [None, 10]}
    assert variants[-1]["properties"]["value"]["properties"]["group_overrides"]["type"] == "array"
    assert schema["properties"]["sources"]["items"]["enum"] == ["catalog:vacations-2", "case:7"]
    assert schema["properties"]["assignments"]["anyOf"][1]["properties"]["vacations"]["items"]["properties"]["group"]["enum"] == ["Operacional"]

def test_structured_values_are_checked_against_the_catalog_schema(setup):
    _, fake, _ = setup
    snapshot = fake.snapshot("2026-10-31", "EMP001")
    catalog = {"version": "workflow-2", "module": "workflow", "contract_hash": "a" * 64, "scopes": [{"type": "company"}],
               "fields": [{"id": "approval_flow", "label": "Fluxo", "type": "object", "schema": WORKFLOW_SCHEMA}]}
    snapshot["effective"]["homologation"] = [{"group_id": None, "group_name": "Regra geral", "configuration": {"approval_flow": {"steps": [{"approver": "hr", "sla_hours": 16}], "group_overrides": {}}}}]
    good = Proposal(summary="Duas etapas", questions=[], tests=["Conferir etapas"], sources=["catalog:workflow-2"],
                    changes=[{"parameter": "approval_flow", "value": {"steps": [{"approver": "direct_manager", "sla_hours": 8}, {"approver": "hr", "sla_hours": 16}], "group_overrides": {}}, "reason": "Pedido"}])
    class Validator:
        def validate(self, snapshot, payloads, assignments=None):
            return {"valid": True, "errors": []}
    payloads, assignments = checked_payloads(good, snapshot, catalog, Validator())
    assert assignments is None and len(payloads[0]["approval_flow"]["steps"]) == 2 and "config_code" not in payloads[0]
    bad = copy.deepcopy(good); bad.changes[0].value = {"steps": [{"approver": "ceo", "sla_hours": 8}], "group_overrides": {}}
    with pytest.raises(HTTPException) as failure:
        checked_payloads(bad, snapshot, catalog, Validator())
    assert failure.value.status_code == 422 and any("ceo" in error for error in failure.value.detail["errors"])


def test_hr_module_snapshot_without_assignments_reaches_interpretation(setup, monkeypatch, tmp_path):
    """HR modules publish assignments=null and no named_configurations; the interpreter must not assume the vacations shape."""
    import copy, json
    from app import orchestration as flow
    client, fake, _ = setup
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.setattr(flow.provider, "read_config", lambda: {"api_key": "fake-only", "base_url": "https://example.invalid/v1", "model": "unused"})
    options = {"explanation": "Interpretações.", "options": [{"title": f"Opção {i}", "meaning": f"Significado distinto número {i} para a regra de ponto.", "example": "Exemplo.", "support": "supported", "limitations": []} for i in range(3)]}
    def completion(config, messages, schema, *, name, **kwargs):
        if name == "module_classifier":
            return {"module": "time", "explanation": "Ponto.", "company_query": "ABC Ltda", "scope": "parametrization"}, {"mode": "model", "model": config["model"], "usage": {}}
        return copy.deepcopy(options), {"mode": "model", "model": config["model"], "usage": {}}
    monkeypatch.setattr(flow.provider, "completion", completion)
    original = fake.snapshot
    def module_snapshot(as_of, company_code):
        snapshot = original(as_of, company_code)
        snapshot.pop("named_configurations"); snapshot["assignments"] = None; snapshot["groups"] = []
        snapshot["effective"] = {env: rows[:1] for env, rows in snapshot["effective"].items()}
        return snapshot
    monkeypatch.setattr(fake, "snapshot", module_snapshot)
    fake.catalog_overrides = {"module": "timekeeping", "module_label": "Controle de ponto", "version": "timekeeping-2", "scopes": [{"type": "company"}]}
    row = client.post("/api/cases", json={"request": "Desabilitar horas extras no ponto da ABC Ltda", "effective_from": "2026-10-31"}).json()
    response = client.post(f"/api/cases/{row['id']}/orchestrate", json={"revision": row["revision"]})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "awaiting_interpretation" and response.json()["module"] == "timekeeping"
