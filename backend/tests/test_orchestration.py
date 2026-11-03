import copy
import json
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from app import orchestration as flow
from app.contracts import Proposal
from app.models import InterpretationMemory
from test_workflow import setup, PROPOSAL

# One suggestion with one doubtful item: the suggested reading is configurable, the other two are not in the catalog.
SUGGESTION = {"explanation": "Antecedência de quinze dias para pedir férias.", "items": [
    {"id": "R1", "text": "Quinze dias de antecedência para pedir férias", "candidates": [], "question": "Como contar os quinze dias?", "readings": [
        {"label": "Pelo menos quinze dias", "status": "change", "modules": ["vacations"], "how": "Pedidos com pelo menos quinze dias de antecedência, na regra geral da empresa ABC.", "warning": ""},
        {"label": "No máximo quinze dias", "status": "gap", "modules": ["vacations"], "how": "Antecedência máxima para solicitação.", "warning": ""},
        {"label": "Exatamente quinze dias", "status": "gap", "modules": ["vacations"], "how": "Janela exata de solicitação.", "warning": ""}]}]}

@pytest.fixture
def agents(setup, monkeypatch, tmp_path):
    monkeypatch.setenv("LANGGRAPH_SQLITE_PATH", str(tmp_path / "checkpoints.db"))
    calls = []
    monkeypatch.setattr(flow.provider, "read_config", lambda: {"api_key": "fake-only", "base_url": "https://example.invalid/v1", "model": "unused"})
    def completion(config, messages, schema, *, name, **kwargs):
        calls.append((name, config["model"], config.get("reasoning_effort")))
        request = json.loads(messages[-1]["content"]).get("request", "")
        result = {"module": "benefits" if "benefícios" in request.lower() else "vacations", "explanation": "Assunto identificado.", "company_query": "Outra Ltda" if "Outra" in request else "ABC Ltda", "scope": "parametrization"} if name == "module_classifier" else copy.deepcopy(SUGGESTION)
        return result, {"mode": "model", "model": config["model"], "usage": {}, "duration_seconds": 0.1}
    monkeypatch.setattr(flow.provider, "completion", completion)
    def generate(*args, config=None, **kwargs):
        calls.append(("plan", config["model"], config.get("reasoning_effort")))
        return Proposal.model_validate(PROPOSAL), {"mode": "model", "model": config["model"], "usage": {}, "duration_seconds": 0.1}
    monkeypatch.setattr(flow.provider, "generate", generate)
    return (*setup, calls)

def post(client, row, endpoint="workflow", **data):
    response = client.post(f"/api/cases/{row['id']}/{endpoint}", json={"revision": row["revision"], **data})
    assert response.status_code == 200, response.text
    return response.json()

def start(client):
    row = client.post("/api/cases", json={"request": "Configurar quinze dias antes das férias na ABC", "effective_from": "2026-10-31"}).json()
    return post(client, row, "orchestrate")

def options(client, row, module="vacations"):
    return row if module == "vacations" else post(client, row, "orchestrate", comment="Correção do solicitante: o módulo correto é benefícios.")

def test_durable_pauses_confirmation_and_separate_memory(agents):
    client, fake, factory, calls = agents
    row = start(client)
    assert row["status"] == "awaiting_interpretation"
    assert len(calls) == 2
    # A new HTTP invocation recompiles the graph and reopens the file checkpointer.
    row = options(client, row)
    assert row["status"] == "awaiting_interpretation"
    assert len(row["workflow"]["suggestion"]["items"][0]["readings"]) == 3
    assert len(calls) == 2
    before = copy.deepcopy(fake.data)
    row = post(client, row, action="choose", choice="accept")
    assert row["status"] == "review" and row["approval_hash"]
    assert len(calls) == 3  # resuming a choice does not repeat classifier or alternatives
    assert fake.data == before  # selection never applies a configuration
    with factory() as db:
        memory = db.scalar(select(InterpretationMemory))
        assert memory.meaning == flow.accepted(SUGGESTION["items"], {})["meaning"]
        assert memory.actor == "consultor" and memory.module == "vacations"
    assert client.get("/api/interpretation-memories").json()[0]["active"]
    assert client.post("/api/interpretation-memories/1/forget", json={}).status_code == 200
    row2 = options(client, start(client))
    assert row2["workflow"]["memories"] == []

def test_none_escalates_once_then_consultant(agents):
    client, fake, factory, calls = agents
    row = options(client, start(client))
    row = post(client, row, action="choose", choice="none", comment="Nenhuma atende, preciso de outra regra.")
    assert row["workflow"]["attempt"] == 2
    assert calls[-1] == ("interpretation_agent", "gpt-6.1-sol", "high")
    row = post(client, row, action="choose", choice="none")
    assert row["status"] == "consultant_queue"
    assert len(calls) == 4
    assert client.get("/api/consultant-queue").json()[0]["id"] == row["id"]
    assert not client.get("/api/interpretation-memories").json()
    row = post(client, row, action="expert", comment="Permitir três parcelas para toda a empresa ABC.")
    assert row["status"] == "review"
    assert calls[-1] == ("plan", "gpt-6.1-sol", "high")
    assert not client.get("/api/consultant-queue").json()

def test_missing_capability_goes_to_consultant_without_plan(agents):
    client, fake, factory, calls = agents
    row = options(client, start(client))
    row = post(client, row, action="choose", choice="accept", readings={"R1": 1})
    assert row["status"] == "consultant_queue"
    assert len(calls) == 2 and not row["proposal"]
    row = post(client, row, action="close", comment="O HCM precisa de parâmetro de antecedência máxima.")
    assert row["status"] == "consultant_closed"
    assert row["workflow"]["resolution"]

def test_other_module_cannot_reach_vacation_writes(agents):
    client, fake, factory, calls = agents
    row = options(client, start(client), module="benefits")
    assert row["status"] == "consultant_queue"
    response = client.post(f"/api/cases/{row['id']}/workflow", json={"revision": row["revision"], "action": "expert", "comment": "Criar nova configuração de benefícios."})
    assert response.status_code == 409
    assert not row["proposal"] and len(calls) == 3

def test_retry_uses_checkpoint_without_repeating_accepted_choice(agents, monkeypatch):
    client, fake, factory, calls = agents
    real = flow.provider.generate
    failed = [False]
    def transient(*args, **kwargs):
        if not failed[0]:
            failed[0] = True
            raise HTTPException(504, "Timeout simulado")
        return real(*args, **kwargs)
    monkeypatch.setattr(flow.provider, "generate", transient)
    row = options(client, start(client))
    response = client.post(f"/api/cases/{row['id']}/workflow", json={"revision": row["revision"], "action": "choose", "choice": "accept"})
    assert response.status_code == 504
    row = client.get(f"/api/cases/{row['id']}").json()
    assert row["status"] == "workflow_error"
    row = post(client, row, action="retry")
    assert row["status"] == "review"
    assert len(calls) == 3
    assert len(client.get("/api/interpretation-memories").json()) == 1

def test_stale_choice_and_wrong_phase_rejected(agents):
    client, *_ = agents
    row = start(client)
    wrong = client.post(f"/api/cases/{row['id']}/workflow", json={"revision": row["revision"], "action": "guide"})
    assert wrong.status_code == 409
    updated = post(client, row, action="choose", choice="accept")
    stale = client.post(f"/api/cases/{row['id']}/workflow", json={"revision": row["revision"], "action": "choose", "choice": "accept"})
    assert stale.status_code == 409 and updated["status"] == "review"


def test_memory_is_scoped_by_actor_company_catalog_and_module(agents):
    client, fake, factory, calls = agents
    row = post(client, options(client, start(client)), action="choose", choice="accept")
    nextrow = options(client, start(client))
    assert len(nextrow["workflow"]["memories"]) == 1
    with factory() as db:
        from test_workflow import CATALOG
        base = {"actor": "consultor", "company": "EMP001", "module": "vacations", "catalog": {"version": CATALOG["version"], "contract_hash": CATALOG["contract_hash"]}, "case_id": 999, "request": row["request"]}
        assert len(flow.memories_for(db, base)) == 1
        for overrides in ({"actor": "other"}, {"company": "other"}, {"module": "benefits"}, {"catalog": {"version": "future"}}, {"catalog": {"version": CATALOG["version"], "contract_hash": "0" * 64}}):
            assert flow.memories_for(db, {**base, **overrides}) == []

def test_consultant_can_submit_validated_manual_resolution(agents):
    client, *_ = agents
    row = post(client, options(client, start(client)), action="choose", choice="accept", readings={"R1": 1})
    assert row["workflow"]["catalog"]["version"] == "vacations-2"
    row = post(client, row, "manual-proposal", proposal=PROPOSAL, comment="Proposta revisada pelo consultor.")
    assert row["status"] == "review" and row["workflow"]["phase"] == "review"
    assert row["workflow"]["manual_resolution"]
    assert client.get("/api/consultant-queue").json() == []

def test_corrected_plan_restarts_with_feedback_and_without_old_snapshot(agents):
    client, *_ = agents
    row = post(client, options(client, start(client)), action="choose", choice="accept")
    old_thread = row["workflow"]["thread_id"]
    row = post(client, row, "orchestrate", comment="Corrigir para antecedência mínima de vinte dias.")
    assert row["workflow"]["thread_id"] != old_thread
    assert "vinte dias" in row["workflow"]["request"]
    assert row["proposal"] == {} and row["status"] == "awaiting_interpretation"
    assert not client.get("/api/interpretation-memories").json()[0]["active"]

def test_company_and_module_identified_without_confirmation(agents):
    client, _, _, calls = agents
    row = start(client)
    assert row["status"] == "awaiting_interpretation" and len(calls) == 2
    assert row["workflow"]["confirmed_company"]["legal_name"] == "ABC Ltda"
    assert row["workflow"]["module"] == "vacations"
    assert len(row["workflow"]["suggestion"]["items"]) == 1


@pytest.mark.parametrize("kind", ["missing", "ambiguous"])
def test_unresolved_company_asks_only_when_necessary(agents, monkeypatch, kind):
    client, fake, _, calls = agents
    original = fake.companies
    base = {"id": 2, "code": "EMP002", "name": "Outra", "legal_name": "Outra Ltda", "city": "Recife", "state": "PE", "configuration_allowed": False}
    items = [] if kind == "missing" else [base] if kind == "unauthorized" else [dict(base, name="ABC Comércio", legal_name="ABC Comércio Ltda"), dict(base, code="EMP003", name="ABC Serviços", legal_name="ABC Serviços Ltda")]
    monkeypatch.setattr(fake, "companies", lambda query="": {"items": items})
    row = start(client)
    assert row["status"] == "awaiting_context" and row["workflow"]["phase"] == "clarify"
    assert row["snapshot"] == {} and row["workflow"]["confirmed_company"] == {}
    assert len(calls) == 1
    monkeypatch.setattr(fake, "companies", original)
    row = post(client, row, action="clarify", comment="A empresa correta é ABC Ltda.")
    assert row["status"] == "awaiting_interpretation"


def test_none_reclassifies_module_from_user_correction(agents):
    client, *_ = agents
    row = start(client)
    row = post(client, row, action="choose", choice="none", comment="Entendeu errado: é sobre benefícios da ABC Ltda.")
    assert row["status"] == "consultant_queue"
    assert row["workflow"]["module"] == "benefits"
    assert row["snapshot"] == {}

def test_arbitrary_company_is_used_through_plan_execution_approval_and_rollback(agents):
    from test_execution_flow import Executor
    from app.models import Case
    from app.knowledge import retrieve
    from test_workflow import CATALOG
    client, fake, factory, calls = agents
    fake.data["company"] = {"id": 7, "code": "234234SZDFA", "name": "Outra"}
    for rows in fake.data["schedules"].values():
        for config in rows:
            config["company_id"] = 7
            config["scope_key"] = "company:7:default"
    baseline = copy.deepcopy(fake.data)
    with factory() as db:
        db.add(Case(request="parcelas férias aprovação", effective_from="2026-10-31", company_code="EMP001", module="vacations", catalog_version=CATALOG["version"], contract_hash=CATALOG["contract_hash"], status="validated", proposal=PROPOSAL))
        db.commit()
        shared = retrieve(db, "parcelas férias aprovação", CATALOG, company_code="234234SZDFA")
        assert len(shared) == 2 and shared[1]["id"].startswith("shared:") and "EMP001" not in json.dumps(shared[1])  # other company only as redacted analogy
    row = client.post("/api/cases", json={"request": "Configurar férias da Outra Ltda em três parcelas", "effective_from": "2026-10-31"}).json()
    assert row["company_code"] == ""
    row = post(client, row, "orchestrate")
    assert row["company_code"] == "234234SZDFA"
    row = post(client, row, action="choose", choice="accept")
    assert row["proposal"]["payloads"][0]["company_id"] == 7
    executor = Executor(fake)
    fake.apply, fake.verify, fake.rollback = executor.apply, executor.verify, executor.rollback
    row = post(client, row, "execute", approval_hash=row["approval_hash"])
    assert row["result"]["execution_request"]["packets"][0]["company_code"] == "234234SZDFA"
    row = post(client, row, "verify")
    row = post(client, row, "confirm-result")
    assert row["status"] == "validated"
    row = post(client, row, "rollback")
    assert row["status"] == "reverted" and fake.data == baseline

def test_missing_capabilities_are_automatically_registered_in_unhandled(agents, monkeypatch):
    client, _, _, calls = agents
    original = flow.provider.completion
    def unsupported(*args, **kwargs):
        value, metrics = original(*args, **kwargs)
        if kwargs["name"] == "interpretation_agent":
            for reading in value["items"][0]["readings"]:
                reading["status"] = "gap"
            value["items"][0]["readings"][0]["how"] = "Limite anual de solicitações de férias."
        return value, metrics
    monkeypatch.setattr(flow.provider, "completion", unsupported)
    row = start(client)
    assert row["status"] == "consultant_queue" and not row["proposal"]
    item = client.get("/api/unhandled").json()[0]
    assert item["id"] == row["id"] and item["company"] == "ABC"
    assert item["category"] == "missing_capability" and "anual" in item["reason"]
    assert row["workflow"]["capabilities"] == [{"module": "vacations", "description": "Limite anual de solicitações de férias."}]
    row = post(client, row, action="close", comment="É necessário disponibilizar a capacidade de limitar solicitações por ano.")
    assert row["status"] == "consultant_closed"
    assert not client.get("/api/unhandled").json()

def test_unresolved_identification_can_be_sent_to_internal_queue(agents, monkeypatch):
    client, fake, _, _ = agents
    monkeypatch.setattr(fake, "companies", lambda query="": {"items": []})
    row = start(client)
    row = post(client, row, action="handoff", comment="Consultor deve verificar o cadastro.")
    assert row["status"] == "consultant_queue"
    assert client.get("/api/unhandled").json()[0]["category"] == "identification"

def test_hcm_client_is_bound_to_the_case_module(agents):
    from types import SimpleNamespace
    from app.hcm import get_hcm
    from app.models import Case
    _, _, factory, _ = agents
    with factory() as db:
        row=Case(request="Bloquear horas extras",effective_from="2026-10-31",company_code="234234SZDFA",workflow={"module":"time"})
        db.add(row);db.commit()
        hcm=get_hcm(SimpleNamespace(path_params={"case_id":row.id}),db)
        assert hcm.module=="timekeeping" and hcm.path=="/modules/timekeeping"
        assert hcm.for_module("benefits").path=="/modules/benefits"

def test_catalog_string_parameters_are_validated_as_strings(agents):
    from types import SimpleNamespace
    from app.workflow import checked_payloads
    from test_workflow import base_snapshot
    snapshot=base_snapshot();snapshot["groups"]=[]
    snapshot["effective"]["homologation"]=[{"group_id":None,"configuration":{"currency":"BRL"}}]
    catalog={"fields":[{"id":"currency","type":"string","label":"Moeda","schema":{"type":"string","minLength":3,"maxLength":3}}],"scopes":[{"type":"company"}]}
    proposal=Proposal(summary="Alterar moeda de apresentação",questions=[],changes=[{"group_id":None,"parameter":"currency","value":"USD","reason":"Pedido explícito do usuário"}],tests=["Conferir moeda"],sources=["catalog:payroll-1"])
    payloads,assignments=checked_payloads(proposal,snapshot,catalog,SimpleNamespace(validate=lambda *args:{"valid":True}))
    assert payloads[0]["currency"]=="USD" and assignments is None and "config_code" not in payloads[0]
    proposal.changes[0].value="US"
    with pytest.raises(HTTPException): checked_payloads(proposal,snapshot,catalog,SimpleNamespace(validate=lambda *args:{"valid":True}))
