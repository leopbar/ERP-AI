"""Phase 4: stemmed retrieval, redacted cross-company experience, lessons as counterexamples, knowledge
switches, aggregated metrics and structured capability gaps."""
import copy
import json
from sqlalchemy import select
from test_workflow import setup, opened, manual, action, CATALOG, PROPOSAL  # noqa: F401
from test_orchestration import agents, post, start, options  # noqa: F401
from app import orchestration as flow
from app.contracts import Proposal
from app.knowledge import redact, retrieve, stem, terms
from app.models import Case, Lesson


def test_portuguese_stemming_groups_inflections():
    assert stem("parcelas") == stem("parcela") == stem("parcelamento")
    assert stem("configuracoes") == stem("configuracao")
    assert terms("Permitir três parcelas de férias com aprovação") & terms("Parcelamento das férias aprovado") >= {stem("parcelas"), stem("ferias"), stem("aprovacao")}
    assert "para" not in terms("para a empresa")


def test_redaction_removes_company_and_group_names():
    text = redact("A ABC Ltda (ABC, EMP001) quer três parcelas para o grupo Operacional", {"name": "ABC", "legal_name": "ABC Ltda", "code": "EMP001"}, [{"name": "Operacional"}])
    assert "ABC" not in text and "EMP001" not in text and "Operacional" not in text
    assert "[empresa]" in text and "[grupo]" in text


def validated_case(factory, company_code, request, summary):
    with factory() as db:
        row = Case(request=request, effective_from="2026-10-31", company_code=company_code, module="vacations", catalog_version=CATALOG["version"], contract_hash=CATALOG["contract_hash"],
                   status="validated", proposal={**PROPOSAL, "summary": summary}, snapshot={"company": {"id": 9, "code": company_code, "name": "Outra"}, "groups": [{"id": 3, "name": "Diretoria"}]},
                   events=[{"type": "validated", "comment": "Aprovado pela Outra"}])
        db.add(row); db.commit()
        return row.id


def test_cross_company_experience_is_redacted_and_switchable(setup):
    client, fake, factory = setup
    validated_case(factory, "EMP999", "Permitir três parcelas de férias na Outra para a Diretoria", "Três parcelas para a Outra")
    with factory() as db:
        sources = retrieve(db, "Permitir três parcelas de férias", CATALOG, company_code="EMP001")
    shared = [s for s in sources if s["id"].startswith("shared:")]
    assert len(shared) == 1 and "Outra" not in json.dumps(shared[0]) and "EMP999" not in json.dumps(shared[0]) and "Diretoria" not in json.dumps(shared[0])
    assert shared[0]["content"]["changes"] == [{"scope": "company", "parameter": "max_installments", "value": 3}]
    assert client.get("/api/knowledge/settings").json() == {"cross_company": True, "lessons": True}
    assert client.post("/api/knowledge/settings", json={"cross_company": False, "lessons": True}).json()["cross_company"] is False
    with factory() as db:
        assert not [s for s in retrieve(db, "Permitir três parcelas de férias", CATALOG, company_code="EMP001") if s["id"].startswith("shared:")]


def test_rejections_and_consultant_resolutions_become_lessons(agents):
    client, fake, factory, calls = agents
    row = options(client, start(client))
    row = post(client, row, action="choose", choice="none", comment="Nenhuma atende: a ABC quer antecedência máxima, não mínima.")
    row = post(client, row, action="choose", choice="none", comment="Encaminhar.")
    row = post(client, row, action="close", comment="O HCM não possui antecedência máxima; a ABC Ltda deve pedir essa capacidade.")
    with factory() as db:
        lessons = db.scalars(select(Lesson).order_by(Lesson.id)).all()
        kinds = [lesson.kind for lesson in lessons]
        assert "interpretation_rejected" in kinds and "consultant_resolution" in kinds
        assert all("ABC" not in lesson.text and "[empresa]" in lesson.text for lesson in lessons)
        assert all(lesson.module == "vacations" and lesson.contract_hash == CATALOG["contract_hash"] for lesson in lessons)
        sources = retrieve(db, "Antecedência máxima de quinze dias antes das férias", CATALOG, company_code="EMP001")
        assert any(s["id"].startswith("lesson:") for s in sources)
    client.post("/api/knowledge/settings", json={"cross_company": True, "lessons": False})
    with factory() as db:
        assert not any(s["id"].startswith("lesson:") for s in retrieve(db, "Antecedência máxima de quinze dias antes das férias", CATALOG, company_code="EMP001"))


def test_validator_rejections_are_recorded_and_offered_to_the_interpreter(agents, monkeypatch):
    client, fake, factory, calls = agents
    def generate(request, snapshot, sources, feedback, config=None, **kwargs):
        invalid = copy.deepcopy(PROPOSAL); invalid["changes"][0]["value"] = 2
        return Proposal.model_validate(invalid), {"mode": "model", "model": config["model"], "usage": {}}
    monkeypatch.setattr(flow.provider, "generate", generate)
    row = post(client, options(client, start(client)), action="choose", choice="accept")
    assert row["status"] == "consultant_queue" and len(row["workflow"]["repair_notes"]) == 2
    with factory() as db:
        kinds = {lesson.kind for lesson in db.scalars(select(Lesson)).all()}
    assert {"validator_rejection", "validation_failed"} <= kinds
    seen = {}
    original = flow.provider.completion
    def completion(*args, **kwargs):
        if kwargs["name"] == "interpretation_agent":
            seen["lessons"] = json.loads(args[1][-1]["content"])["lessons"]
        return original(*args, **kwargs)
    monkeypatch.setattr(flow.provider, "completion", completion)
    again = client.post("/api/cases", json={"request": "Configurar quinze dias antes das férias na ABC, três parcelas", "effective_from": "2026-10-31"}).json()
    post(client, again, "orchestrate")
    assert seen["lessons"] and any(item["kind"] == "validator_rejection" for item in seen["lessons"])


def test_metrics_and_capability_gaps_endpoints(agents):
    client, fake, factory, calls = agents
    row = options(client, start(client))
    row = post(client, row, action="choose", choice="accept", readings={"R1": 1})
    assert row["status"] == "consultant_queue"
    metrics = client.get("/api/metrics").json()
    assert metrics["totals"]["cases"] == 1 and metrics["totals"]["validated"] == 0 and metrics["totals"]["cost_per_validated_case"] is None
    roles = {(m["model"], m["role"]) for m in metrics["by_model"]}
    assert ("gpt-6-luna", "module_classifier") in roles and ("gpt-6-luna", "interpretation_agent") in roles
    module = next(m for m in metrics["by_module"] if m["module"] == "vacations")
    assert module["cases"] == 1 and module["first_round_accepted"] == 1 and module["consultant"] == 1
    gaps = client.get("/api/unhandled/capabilities").json()
    assert gaps == [{"module": "vacations", "description": "Antecedência máxima para solicitação.", "count": 1, "open": 1, "case_ids": [row["id"]]}]
    csv = client.get("/api/unhandled/capabilities?format=csv")
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv") and "Antecedência máxima" in csv.text
    post(client, row, action="close", comment="Capacidade registrada para o roadmap do HCM.")
    assert client.get("/api/unhandled/capabilities").json()[0]["open"] == 0
