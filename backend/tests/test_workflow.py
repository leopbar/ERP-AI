import copy
import hashlib
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.auth import password_hash
from app.contracts import Proposal
from app.database import get_db
from app.hcm import get_hcm
from app.main import app, login_attempts
from app.models import Base, Case
from app.knowledge import retrieve
from app.provider import generate
from app.workflow import checked_payloads

FIELDS = {"process_enabled": True, "employee_request_enabled": True, "manager_request_enabled": True,
          "manager_approval_required": True, "simulation_enabled": True, "installments_allowed": True,
          "max_installments": 2, "min_advance_days": 30, "allow_13th_advance": False, "show_balance_to_employee": True}
NAMED = ("config_code", "name", "simulation_text")

def field_entry(name, value):
    if type(value) is bool:
        return {"id": name, "label": name, "type": "boolean", "schema": {"type": "boolean"}, "minimum": None, "maximum": None}
    low, high = (1, 3) if name == "max_installments" else (0, 365)
    return {"id": name, "label": name, "type": "integer", "schema": {"type": "integer", "minimum": low, "maximum": high}, "minimum": low, "maximum": high}

RULES = [  # same texts the HCM publishes in its vacations catalog, so synthetic evaluations see what the real flow sees
    "Precedência por colaborador: 1) Atribuições de Configurações, na ordem da lista; 2) regra própria de um dos grupos do colaborador; 3) regra da empresa; 4) padrão do sistema.",
    "Regra de grupo, de empresa ou nomeada é completa: não há mesclagem por campo entre níveis.",
    "Configurações nomeadas têm código e descrição, não são ligadas diretamente a um grupo e só valem quando atribuídas em Atribuições de Configurações.",
    "Sem parcelamento, max_installments deve ser 1; com parcelamento, deve ser 2 ou 3.",
    "Com processo desabilitado, solicitações por funcionário e gestor devem estar desabilitadas.",
    "Aprovação atual é feita pelo perfil Gestor de RH ou administrador; etapas e SLA ficam no módulo workflow (processes.vacations).",
    "Parcelas são um limite demonstrativo; não há agendamento de períodos separados.",
]
CATALOG = {"version": "vacations-2", "contract": 2, "module": "vacations", "module_label": "Férias", "erp_version": "0.2.0", "rules": RULES,
           "contract_hash": hashlib.sha256(b"vacations-2-test-contract").hexdigest(),
           "scopes": [{"type": "company", "id_field": None}, {"type": "group", "id_field": "group_id"}, {"type": "config", "id_field": "config_code"}],
           "precedence": [{"level": 1, "scope": "assignment"}, {"level": 2, "scope": "group"}, {"level": 3, "scope": "company"}, {"level": 4, "scope": "system"}],
           "fields": [field_entry(name, value) for name, value in FIELDS.items()]}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def scope_key_of(company_id, row):
    if row.get("config_code"):
        return f"company:{company_id}:config:{row['config_code']}"
    return f"company:{company_id}:group:{row['group_id']}" if row.get("group_id") is not None else f"company:{company_id}:default"

def base_data():
    """Raw HCM state the fake keeps; the snapshot (effective rules, fingerprint) is derived from it on every call."""
    config = {"id": 1, "scope_key": "company:1:default", "company_id": 1, "group_id": None, "version": 1, "effective_from": "2026-01-01", "active": True,
              "config_code": None, "name": None, "simulation_text": None, **FIELDS}
    return {"company": {"id": 1, "code": "EMP001", "name": "ABC"}, "as_of": "2026-10-31",
            "groups": [{"id": 10, "name": "Operacional", "employee_count": 2, "primary_count": 2, "member_count": 2, "overlap_count": 0}],
            "schedules": {environment: [{**config, "environment": environment}] for environment in ("homologation", "production")},
            "assignments": {environment: {"vacations": [], "vacations_simulation": [], "version": 0, "effective_from": None} for environment in ("homologation", "production")}}

def resolve(data, as_of=None):
    """Same precedence as the HCM: ordered assignments, group rule, company rule, system default."""
    as_of = as_of or data["as_of"]
    company_id = data["company"]["id"]
    snapshot = {"contract": 2, "company": data["company"], "catalog_version": "vacations-2", "contract_hash": CATALOG["contract_hash"], "erp_version": "0.2.0",
                "as_of": as_of, "consulted_at": "2026-10-31T10:00:00+00:00", "groups": copy.deepcopy(data["groups"]), "schedules": copy.deepcopy(data["schedules"]),
                "assignments": copy.deepcopy(data["assignments"]), "named_configurations": {}, "effective": {},
                "effective_basis": "Regra resolvida para um colaborador apenas do grupo indicado."}
    for environment, rows in data["schedules"].items():
        latest = {}
        for row in sorted(rows, key=lambda item: (item["effective_from"], item["version"])):
            if row["effective_from"] <= as_of:
                latest[row["scope_key"]] = row
        snapshot["named_configurations"][environment] = [row for row in latest.values() if row.get("config_code")]
        general = latest.get(f"company:{company_id}:default")
        def origin_for(scope, row, extra=None):
            return {"scope": scope, "label": {"company": f"Empresa {data['company']['name']}", "group": "Grupo", "assignment": f"Configuração {row.get('config_code')}"}[scope], "scope_key": row["scope_key"], "version": row["version"], **(extra or {})}
        effective = [{"group_id": None, "group_name": "Regra geral", "configuration": general, "origin": origin_for("company", general) if general else None, "why": "", "overlap_count": 0}]
        for group in data["groups"]:
            chosen, origin = None, None
            for index, item in enumerate(data["assignments"][environment]["vacations"], 1):
                if item["group"].lower() == group["name"].lower() and latest.get(f"company:{company_id}:config:{item['config']}"):
                    chosen = latest[f"company:{company_id}:config:{item['config']}"]; origin = origin_for("assignment", chosen, {"group": group["name"], "priority": index}); break
            if chosen is None and latest.get(f"company:{company_id}:group:{group['id']}"):
                chosen = latest[f"company:{company_id}:group:{group['id']}"]; origin = {**origin_for("group", chosen), "label": f"Grupo {group['name']}"}
            if chosen is None and general is not None:
                chosen, origin = general, origin_for("company", general)
            effective.append({"group_id": group["id"], "group_name": group["name"], "configuration": chosen, "origin": origin, "why": "", "overlap_count": group.get("overlap_count", 0)})
        snapshot["effective"][environment] = effective
    snapshot["fingerprint"] = digest({key: snapshot[key] for key in ("schedules", "as_of", "catalog_version", "erp_version", "groups", "company", "assignments")})
    return snapshot

def base_snapshot():
    return resolve(base_data())

PROPOSAL = {"summary": "Aumentar limite para três parcelas", "questions": [], "changes": [{"group_id": None, "parameter": "max_installments", "value": 3, "reason": "Atender o pedido"}], "tests": ["Conferir três parcelas", "Conferir aprovação obrigatória"], "sources": ["catalog:vacations-2"]}

class FakeHCM:
    module = "vacations"
    def __init__(self):
        self.data = base_data()
        self.catalog_overrides = {}
    def for_module(self, module):
        return self
    def modules(self):
        return [{"id": "vacations", "label": "Férias", "description": "Regras de solicitação de férias.", "scopes": ["company", "group", "config"]},
                {"id": "timekeeping", "label": "Controle de ponto", "description": "Jornada, intervalo, tolerância e horas extras.", "scopes": ["company"]},
                {"id": "admission", "label": "Admissão", "description": "Antecedência e documentos da admissão.", "scopes": ["company"]},
                {"id": "payroll", "label": "Folha demonstrativa", "description": "Moeda e aviso da folha demonstrativa.", "scopes": ["company"]},
                {"id": "benefits", "label": "Benefícios", "description": "Processo e solicitação de benefícios.", "scopes": ["company"]},
                {"id": "workflow", "label": "Fluxos de aprovação e SLA", "description": "Etapas de aprovação e prazos de todos os processos.", "scopes": ["company"]}]
    def snapshot(self, as_of, company_code):
        assert company_code == self.data["company"]["code"]
        snapshot = resolve(self.data, str(as_of))
        snapshot["contract_hash"] = self.catalog_overrides.get("contract_hash", snapshot["contract_hash"])  # the real HCM stamps the catalog hash on every snapshot
        return snapshot
    def catalog(self):
        return {**copy.deepcopy(CATALOG), **self.catalog_overrides}
    def companies(self, query=""):
        company = {**self.data["company"], "legal_name": self.data["company"]["name"] + " Ltda", "city": "São Paulo", "state": "SP", "status": "Ativa", "configuration_allowed": True}
        return {"items": [company] if not query or query.lower() in (company["name"].lower(), company["legal_name"].lower(), company["code"].lower()) else [], "has_more": False}
    def validate(self, snapshot, payloads, assignments=None):
        errors = [f"Dependência inválida em {scope_key_of(payload['company_id'], payload)}" for payload in payloads if not (payload["installments_allowed"] or payload["max_installments"] == 1)]
        return {"valid": not errors, "errors": errors, "persisted": False}
    def execute_manual(self):
        config = {**self.data["schedules"]["homologation"][0], "id": 2, "version": 2, "effective_from": "2026-10-31", "max_installments": 3}
        self.data["schedules"]["homologation"].append(config)

@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("CONSULTANT_PASSWORD_HASH", password_hash("testing-password"))
    monkeypatch.setenv("CONSULTANT_USER", "consultor")
    monkeypatch.setenv("APP_ORIGIN", "http://localhost:3001")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_CONFIG_FILE", str(tmp_path / "model.json"))
    login_attempts.clear()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    def db():
        with factory() as session: yield session
    fake = FakeHCM()
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[get_hcm] = lambda: fake
    with TestClient(app) as client:
        client.headers["Origin"] = "http://localhost:3001"
        assert client.post("/api/login", json={"username": "consultor", "password": "testing-password"}).status_code == 200
        yield client, fake, factory
    app.dependency_overrides.clear(); engine.dispose()

def opened(client):
    response = client.post("/api/cases", json={"request": "Permitir três parcelas na ABC com aprovação do RH", "effective_from": "2026-10-31", "company_code": "EMP001"})
    assert response.status_code == 201, response.text
    row = response.json()
    response = client.post(f"/api/cases/{row['id']}/analyze", json={"revision": row["revision"]})
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["status"] == "awaiting_model"
    return row

def manual(client, row, proposal=None):
    return client.post(f"/api/cases/{row['id']}/manual-proposal", json={"revision": row["revision"], "proposal": proposal or PROPOSAL})

def action(client, row, name, **extra):
    return client.post(f"/api/cases/{row['id']}/{name}", json={"revision": row["revision"], **extra})

def test_full_manual_flow_and_retrieval(setup):
    client, fake, factory = setup
    row = opened(client)
    assert row["module"] == "vacations" and row["contract_hash"] == CATALOG["contract_hash"]
    response = manual(client, row); assert response.status_code == 200, response.text
    row = response.json()
    assert row["proposal"]["payloads"][0]["config_code"] is None and row["proposal"]["assignments"] is None
    row = action(client, row, "review", decision="accept").json()
    assert row["status"] == "awaiting_execution"
    response = action(client, row, "check-result"); assert response.status_code == 200
    row = response.json(); assert not row["result"]["configuration_matches"]
    assert action(client, row, "validate", comment="OK", evidence=[]).status_code == 409
    fake.execute_manual()
    row = action(client, row, "check-result").json()
    assert row["result"]["configuration_matches"] and row["result"]["production_unchanged"]
    assert action(client, row, "validate", comment="Aprovado", evidence=[]).status_code == 422
    evidence = [{"test": test, "passed": True, "evidence": "Verificado manualmente no laboratório"} for test in PROPOSAL["tests"]]
    response = action(client, row, "validate", comment="Atende ao pedido", evidence=evidence)
    assert response.status_code == 200, response.text
    row = response.json(); assert row["status"] == "validated"
    with factory() as db:
        assert len(retrieve(db, "parcelas aprovação", CATALOG, company_code="EMP001")) == 2
        assert len(retrieve(db, "parcelas aprovação", {**CATALOG, "contract_hash": "f" * 64}, company_code="EMP001")) == 1
    row = action(client, row, "obsolete", comment="Regra funcional mudou").json()
    with factory() as db:
        assert len(retrieve(db, "parcelas aprovação", CATALOG, company_code="EMP001")) == 1

def test_state_permissions_stale_and_invalid_proposals(setup):
    client, fake, factory = setup
    row = opened(client)
    assert action(client, row, "review", decision="accept").status_code == 409
    assert action(client, row, "check-result").status_code == 409
    invalid = copy.deepcopy(PROPOSAL); invalid["changes"][0]["parameter"] = "invented"
    assert manual(client, row, invalid).status_code == 422
    invalid = copy.deepcopy(PROPOSAL); invalid["changes"][0]["value"] = "3"
    assert manual(client, row, invalid).status_code == 422
    invalid = copy.deepcopy(PROPOSAL); invalid["changes"][0]["value"] = 4
    assert "Valor fora do contrato" in manual(client, row, invalid).json()["detail"]["message"]
    invalid = copy.deepcopy(PROPOSAL); invalid["changes"][0]["group_id"] = 99
    assert manual(client, row, invalid).status_code == 422
    invalid = copy.deepcopy(PROPOSAL); invalid["sources"] = ["case:123"]
    assert manual(client, row, invalid).status_code == 422
    updated = manual(client, row).json()
    assert action(client, row, "review", decision="accept").status_code == 409
    fake.data["schedules"]["production"][0]["version"] += 1
    assert action(client, updated, "review", decision="accept").status_code == 409

def test_auth_and_origin(setup):
    client, _, _ = setup
    client.headers["Origin"] = "http://evil.example"
    assert client.post("/api/cases", json={"request": "Configurar férias", "effective_from": "2026-10-31"}).status_code == 403
    client.cookies.clear()
    assert client.get("/api/cases").status_code == 401

def test_official_dependency_and_production_change(setup):
    client, fake, _ = setup
    row = opened(client)
    invalid = copy.deepcopy(PROPOSAL); invalid["changes"].append({"group_id": None, "parameter": "installments_allowed", "value": False, "reason": "Teste inválido"})
    assert manual(client, row, invalid).status_code == 422
    row = manual(client, row).json()
    row = action(client, row, "review", decision="accept").json()
    fake.execute_manual()
    fake.data["schedules"]["production"][0]["max_installments"] = 3
    row = action(client, row, "check-result").json()
    assert not row["result"]["production_unchanged"]

def test_contract_change_blocks_acceptance_and_connection_rejects_unknown_module(setup):
    client, fake, _ = setup
    row = manual(client, opened(client)).json()
    fake.catalog_overrides = {"contract_hash": "e" * 64}
    assert client.get("/api/connection?module=inexistente").status_code == 422
    assert action(client, row, "check-result").status_code == 409
    response = action(client, row, "review", decision="accept")
    assert response.status_code == 409 and "contrato" in response.json()["detail"].lower()

def test_real_model_adapter_contract(monkeypatch):
    import app.provider as provider
    monkeypatch.setenv("LLM_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("LLM_MODEL", "test-model")
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-key")
    transport = provider.httpx.Client
    def respond(request):
        body = json.loads(request.content)
        schema = body["response_format"]["json_schema"]["schema"]
        assert body["response_format"]["json_schema"]["strict"] is True
        assert "HCM_INTEGRATION_TOKEN" not in request.content.decode()
        change = schema["properties"]["changes"]["items"]["anyOf"][0]
        assert change["properties"]["parameter"]["enum"] == ["process_enabled"] and change["properties"]["value"] == {"type": "boolean"}
        assert "named_configurations" in schema["properties"] and schema["properties"]["assignments"]["anyOf"][0] == {"type": "null"}
        return provider.httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({**PROPOSAL, "named_configurations": [], "assignments": None})}}], "usage": {"prompt_tokens": 100, "completion_tokens": 30}})
    monkeypatch.setattr(provider.httpx, "Client", lambda **kwargs: transport(transport=provider.httpx.MockTransport(respond), **kwargs))
    proposal, metrics = generate("três parcelas", base_snapshot(), [{"id": "catalog:vacations-2", "title": "Catálogo", "content": copy.deepcopy(CATALOG)}], [])
    assert proposal.changes[0].value == 3 and metrics["usage"]["prompt_tokens"] == 100
