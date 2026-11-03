"""Phase 3: agents driven by the HCM catalog: dynamic module list, structured missing capabilities,
one automatic repair round for rejected plans, prompt version per role and catalog-driven reasoning flag."""
import copy
import json
from test_workflow import setup, PROPOSAL  # noqa: F401
from test_orchestration import agents, post, start, options, SUGGESTION  # noqa: F401
from test_workflow import FakeHCM
from app import orchestration as flow
from app.contracts import Proposal
from app.model_catalog import supports_reasoning_effort


def test_classifier_accepts_any_module_published_by_the_hcm(agents, monkeypatch):
    client, fake, _, calls = agents
    original = flow.provider.completion
    def completion(*args, **kwargs):
        value, metrics = original(*args, **kwargs)
        if kwargs["name"] == "module_classifier":
            payload = json.loads(args[1][-1]["content"])
            assert [item["id"] for item in payload["modules"]][-1] == "other" and any(item["id"] == "workflow" and item["description"] for item in payload["modules"])
            value["module"] = "workflow"
        return value, metrics
    monkeypatch.setattr(flow.provider, "completion", completion)
    row = start(client)
    # The fake HCM only serves the vacations catalog, so a workflow request is a connector gap, handled without any fixed list in the ERP.
    assert row["status"] == "consultant_queue" and row["workflow"]["category"] == "missing_connector"
    assert row["workflow"]["module"] == "workflow" and row["workflow"]["module_label"] == "Fluxos de aprovação e SLA"
    assert {item["id"] for item in row["workflow"]["modules"]} == {"vacations", "timekeeping", "admission", "payroll", "benefits", "workflow", "other"}
    assert row["module"] == "workflow"


def test_module_outside_the_hcm_list_is_a_contract_error(agents, monkeypatch):
    client, fake, _, calls = agents
    original = flow.provider.completion
    def completion(*args, **kwargs):
        value, metrics = original(*args, **kwargs)
        if kwargs["name"] == "module_classifier":
            value["module"] = "time"  # alias accepted by the UI, never by the classifier contract
        return value, metrics
    monkeypatch.setattr(flow.provider, "completion", completion)
    row = client.post("/api/cases", json={"request": "Configurar quinze dias antes das férias na ABC", "effective_from": "2026-10-31"}).json()
    response = client.post(f"/api/cases/{row['id']}/orchestrate", json={"revision": row["revision"]})
    assert response.status_code == 502 and "fora do contrato" in response.json()["detail"]
    assert client.get(f"/api/cases/{row['id']}").json()["status"] == "workflow_error"


def test_rejected_plan_gets_one_automatic_repair_before_the_consultant(agents, monkeypatch):
    client, fake, _, calls = agents
    attempts = []
    def generate(request, snapshot, sources, feedback, config=None, **kwargs):
        attempts.append(feedback)
        invalid = copy.deepcopy(PROPOSAL)
        if len(attempts) == 1:
            invalid["changes"][0]["parameter"] = "nonexistent"
            return Proposal.model_validate(invalid), {"mode": "model", "model": config["model"], "usage": {}}
        return Proposal.model_validate(PROPOSAL), {"mode": "model", "model": config["model"], "usage": {}}
    monkeypatch.setattr(flow.provider, "generate", generate)
    row = post(client, options(client, start(client)), action="choose", choice="accept")
    assert row["status"] == "review" and row["proposal"]["payloads"][0]["max_installments"] == 3
    assert len(attempts) == 2 and any("rejeitada pelo validador" in item and "fora do escopo" in item for item in attempts[1])
    planner = [m for m in row["workflow"]["metrics"] if m["role"] == "configuration_agent"]
    assert [m["repair"] for m in planner] == [False, True]
    assert sum(1 for e in row["events"] if e["type"] == "agent_completed" and e["metrics"]["role"] == "configuration_agent") == 2


def test_second_rejection_goes_to_the_consultant_with_the_validator_reason(agents, monkeypatch):
    client, fake, _, calls = agents
    def generate(request, snapshot, sources, feedback, config=None, **kwargs):
        invalid = copy.deepcopy(PROPOSAL); invalid["changes"][0]["value"] = 2  # already the current value: always rejected
        return Proposal.model_validate(invalid), {"mode": "model", "model": config["model"], "usage": {}}
    monkeypatch.setattr(flow.provider, "generate", generate)
    row = post(client, options(client, start(client)), action="choose", choice="accept")
    assert row["status"] == "consultant_queue" and row["workflow"]["category"] == "validation_failed"
    assert "correção automática" in row["workflow"]["reason"] and "já atendido" in row["workflow"]["reason"]
    assert not row["proposal"]


def test_prompt_versions_are_sent_per_role(agents, monkeypatch):
    client, fake, _, calls = agents
    original = flow.provider.completion
    seen = {}
    def completion(*args, **kwargs):
        seen[kwargs["name"]] = kwargs.get("prompt_version")
        return original(*args, **kwargs)
    monkeypatch.setattr(flow.provider, "completion", completion)
    post(client, options(client, start(client)), action="choose", choice="accept")
    assert seen == {"module_classifier": flow.PROMPT_VERSIONS["module_classifier"], "interpretation_agent": flow.PROMPT_VERSIONS["interpretation_agent"]}
    assert flow.PROMPT_VERSIONS["module_classifier"] != flow.PROMPT_VERSIONS["interpretation_agent"] != flow.PROMPT_VERSIONS["configuration_agent"]


def test_reasoning_effort_comes_from_the_model_catalog():
    assert supports_reasoning_effort("gpt-6-luna") and supports_reasoning_effort("gpt-5.4-mini")
    assert not supports_reasoning_effort("gpt-4o") and not supports_reasoning_effort("gpt-4.1-mini")
    assert not supports_reasoning_effort("some-compatible-model")


def test_unsupported_choice_hands_structured_capabilities_to_the_queue(agents):
    client, fake, _, calls = agents
    row = post(client, options(client, start(client)), action="choose", choice="accept", readings={"R1": 1})
    assert row["status"] == "consultant_queue" and row["workflow"]["category"] == "missing_capability"
    assert row["workflow"]["capabilities"] == [{"module": "vacations", "description": "Antecedência máxima para solicitação."}]


def test_interpreter_redirects_to_the_module_that_has_the_capability(agents, monkeypatch):
    """Classifier picks vacation_policy for 'não permitir parcelamento'; the interpreter finds nothing there and names vacations."""
    client, fake, factory, calls = agents
    fake.modules = lambda: [*FakeHCM.modules(fake), {"id": "vacation_policy", "label": "Férias / aviso e abono", "description": "Abono, regras CLT, aviso.", "scopes": ["company"]}]
    base_catalog = fake.catalog
    def catalog_for(module):
        data = base_catalog()
        if fake.current != "vacations":
            data.update(module=fake.current, version=fake.current + "-2", fields=[{"id": "cash_bonus_allowed", "label": "Abono", "type": "boolean", "schema": {"type": "boolean"}}], scopes=[{"type": "company"}])
        return data
    fake.current = "vacations"
    def for_module(module):
        fake.current = module
        return fake
    monkeypatch.setattr(fake, "for_module", for_module)
    monkeypatch.setattr(fake, "catalog", lambda: catalog_for(fake.current))
    original = flow.provider.completion
    def completion(*args, **kwargs):
        value, metrics = original(*args, **kwargs)
        payload = json.loads(args[1][-1]["content"])
        if kwargs["name"] == "module_classifier":
            value["module"] = "vacation_policy"
        elif kwargs["name"] == "interpretation_agent" and payload["module"] == "Férias / aviso e abono":
            assert any(item["id"] == "vacations" for item in payload["modules"]) and payload["already_redirected_from"] == []
            for reading in value["items"][0]["readings"]:
                reading["status"] = "gap"
            value["suggested_module"] = "vacations"
        return value, metrics
    monkeypatch.setattr(flow.provider, "completion", completion)
    row = client.post("/api/cases", json={"request": "Não permitir parcelamento de férias para funcionários da empresa ABC.", "effective_from": "2026-10-31"}).json()
    row = post(client, row, "orchestrate")
    assert row["status"] == "awaiting_interpretation", row["workflow"].get("reason")
    assert row["workflow"]["module"] == "vacations" and row["module"] == "vacations" and row["workflow"]["redirects"] == ["vacation_policy"]
    assert row["workflow"]["classification"]["redirected_to"] == "vacations"
    assert [m["role"] for m in row["workflow"]["metrics"]] == ["module_classifier", "interpretation_agent", "interpretation_agent"]
    assert any(e["type"] == "module_redirected" and e["from_module"] == "vacation_policy" and e["to_module"] == "vacations" for e in row["events"])
    assert row["snapshot"]["catalog_version"] == "vacations-2"
