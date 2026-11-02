import hmac
import json
import os
import time
from uuid import uuid4
from datetime import date
from typing import Literal
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.auth import Login, check_origin, password_hash, require_user, session_token
from app.contracts import Action, ApplyAction, Finish, ManualProposal, NewCase, Review
from app.database import get_db
from app.hcm import HCMClient, get_hcm, canonical_module
from app.knowledge import retrieve
from app.models import Case, now
from app.provider import configured, generate, connection_test
from app.model_settings import ModelSettingsInput, public_settings, save_settings, record_test, read_config
from app.model_catalog import list_models
from app.workflow import checked_payloads, compare_result, fingerprint, approval_hash

@asynccontextmanager
async def lifespan(application):
    from app.orchestration import checkpointer
    with checkpointer(initialize=True):
        pass
    yield

app = FastAPI(title="ERP AI — Parametrização assistida", version="0.3.0", lifespan=lifespan)
login_attempts = {}

def save(db):
    try:
        db.commit()
    except StaleDataError:
        db.rollback()
        raise HTTPException(409, "Atendimento mudou em outra sessão. Atualize a página.")

def event(row, kind, actor, **data):
    row.events = [*(row.events or []), {"type": kind, "actor": actor, "at": now(), **data}]
    row.updated_at = now()

def load(db, case_id, revision=None):
    row = db.get(Case, case_id)
    if row is None:
        raise HTTPException(404, "Atendimento não encontrado")
    if revision is not None and row.revision != revision:
        raise HTTPException(409, "Atendimento mudou. Atualize antes de continuar.")
    return row

def output(row):
    data = {column.name: getattr(row, column.name) for column in Case.__table__.columns}
    data["approval_hash"] = approval_hash(row.snapshot, row.proposal["payloads"], row.proposal.get("assignments")) if row.snapshot and "payloads" in row.proposal else ""
    return data

def editable(row):
    if row.status in ("validated", "obsolete", "rejected"):
        raise HTTPException(409, "Atendimento encerrado; abra um novo atendimento")
    if row.status in ("executing", "execution_unknown", "applied", "verification_failed", "rollback_unknown"):
        raise HTTPException(409, "Consulte ou retome a execução pendente antes de iniciar outra análise")

def fresh(row, hcm):
    current = hcm.snapshot(row.effective_from, row.company_code)
    if current.get("contract_hash", "") != (row.contract_hash or ""):
        raise HTTPException(409, "O contrato de integração com o HCM mudou desde a análise. Analise novamente antes de aceitar.")
    if fingerprint(current) != fingerprint(row.snapshot):
        raise HTTPException(409, "O HCM mudou desde a análise. Analise novamente antes de aceitar.")

def bind_contract(row, hcm, catalog):
    row.module = hcm.module
    row.catalog_version = catalog["version"]
    row.contract_hash = catalog.get("contract_hash", "")

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.3.0"}

@app.post("/api/login")
def login(payload: Login, request: Request, response: Response):
    check_origin(request)
    peer = request.client.host if request.client else "local"
    timestamp = time.monotonic()
    attempts = [attempt for attempt in login_attempts.get(peer, []) if timestamp - attempt < 60]
    if len(attempts) >= 10:
        raise HTTPException(429, "Aguarde um minuto antes de tentar novamente")
    login_attempts[peer] = [*attempts, timestamp]
    encoded = os.getenv("CONSULTANT_PASSWORD_HASH", "")
    if ":" not in encoded:
        raise HTTPException(503, "Consultor não configurado")
    candidate = password_hash(payload.password, encoded.split(":")[0])
    if not hmac.compare_digest(candidate, encoded) or not hmac.compare_digest(payload.username, os.getenv("CONSULTANT_USER", "consultor")):
        raise HTTPException(401, "Usuário ou senha inválidos")
    response.set_cookie("erp_session", session_token(), httponly=True, samesite="strict", secure=os.getenv("COOKIE_SECURE") == "true", max_age=28800, path="/")
    login_attempts.pop(peer, None)
    return {"username": payload.username}

@app.post("/api/logout")
def logout(response: Response, user=Depends(require_user)):
    response.delete_cookie("erp_session", path="/")
    return {"ok": True}

@app.get("/api/me")
def me(user=Depends(require_user)):
    return {"username": user, "model_configured": configured(), "execution_enabled": bool(os.getenv("HCM_EXECUTION_TOKEN")), "scope": "Todas as empresas · parâmetros do HCM · homologação"}

@app.get("/api/model")
def model_settings(user=Depends(require_user)):
    return public_settings()

@app.get("/api/model/catalog")
def available_models(user=Depends(require_user)):
    return list_models(read_config())

@app.post("/api/model")
def configure_model(payload: ModelSettingsInput, user=Depends(require_user)):
    return save_settings(payload)

@app.post("/api/model/test")
def test_model(user=Depends(require_user)):
    config, metrics = connection_test()
    report = {"at": now(), "passed": True, "metrics": metrics}
    record_test(config, report)
    return report

@app.get("/api/connection")
def connection(company_code: str = Query(default="", max_length=20), module: str = Query(default="vacations", max_length=40), user=Depends(require_user), hcm=Depends(get_hcm)):
    hcm = hcm.for_module(module) if hasattr(hcm, "for_module") else hcm
    if hasattr(hcm, "modules") and canonical_module(module) not in {item["id"] for item in hcm.modules()}:
        raise HTTPException(422, "Módulo não disponível no catálogo do HCM")
    catalog = hcm.catalog()
    snapshot = hcm.snapshot(date.today(), company_code) if company_code else None
    return {"company": snapshot["company"] if snapshot else None, "catalog": catalog, "snapshot": snapshot, "companies": hcm.companies("")["items"]}

@app.get("/api/cases")
def cases(db: Session = Depends(get_db), user=Depends(require_user)):
    rows = db.scalars(select(Case).order_by(Case.id.desc()).limit(100)).all()
    return [{"id": r.id, "request": r.request, "status": r.status, "revision": r.revision, "updated_at": r.updated_at, "module_label": (r.workflow or {}).get("module_label") or r.module or None, "company_name": (r.workflow or {}).get("confirmed_company", {}).get("name") or (r.snapshot or {}).get("company", {}).get("name")} for r in rows]

@app.post("/api/cases", status_code=201)
def create(payload: NewCase, db: Session = Depends(get_db), user=Depends(require_user)):
    row = Case(request=payload.request.strip(), effective_from=payload.effective_from.isoformat(), company_code=payload.company_code.strip())
    event(row, "opened", user)
    db.add(row); save(db)
    return output(row)

@app.get("/api/cases/{case_id}")
def detail(case_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    return output(load(db, case_id))

@app.post("/api/cases/{case_id}/analyze")
def analyze(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision); editable(row)
    snapshot = hcm.snapshot(row.effective_from, row.company_code)
    catalog = hcm.catalog()
    sources = retrieve(db, row.request, catalog, row.id)
    feedback = [e["comment"] for e in row.events if e.get("comment") and e["type"] in ("correct", "clarification", "proposal_failed")]
    if payload.comment.strip():
        feedback.append(payload.comment.strip())
    metrics = {"mode": "attempted"}
    result = {}
    try:
        proposal, metrics = generate(row.request, snapshot, sources, feedback)
        result = proposal.model_dump() if proposal else {}
        if proposal:
            if not set(proposal.sources) <= {s["id"] for s in sources} or not proposal.sources:
                raise HTTPException(422, "A proposta não referencia fontes recuperadas válidas")
            result["payloads"], result["assignments"] = checked_payloads(proposal, snapshot, catalog, hcm)
    except HTTPException as exc:
        row.snapshot = snapshot; bind_contract(row, hcm, catalog)
        row.proposal = {}; row.result = {}; row.status = "proposal_failed"
        explanation = exc.detail if isinstance(exc.detail, str) else json.dumps(exc.detail, ensure_ascii=False)
        event(row, "proposal_failed", user, proposal=result, sources=sources, metrics=metrics, error=exc.detail,
              comment="A tentativa anterior foi rejeitada. Corrija a proposta considerando: " + explanation)
        save(db)
        raise
    row.snapshot = snapshot; bind_contract(row, hcm, catalog); row.proposal = result; row.result = {}
    row.status = "awaiting_model" if proposal is None else "clarification" if proposal.questions else "review"
    event(row, "analyzed", user, proposal=result, sources=sources, snapshot=snapshot, metrics=metrics)
    if payload.comment.strip():
        event(row, "clarification", user, comment=payload.comment.strip())
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/capture")
def capture(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision); editable(row)
    row.snapshot = hcm.snapshot(row.effective_from, row.company_code)
    catalog = hcm.catalog()
    bind_contract(row, hcm, catalog)
    row.proposal = {}; row.result = {}; row.status = "consulted"
    event(row, "captured", user, snapshot=row.snapshot, sources=retrieve(db, row.request, catalog, row.id), comment=payload.comment)
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/manual-proposal")
def manual(case_id: int, payload: ManualProposal, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision); editable(row)
    if row.workflow and not row.workflow.get("catalog", {}).get("fields"):
        raise HTTPException(409, "Este módulo ainda não possui conector de configuração.")
    if not row.snapshot:
        raise HTTPException(409, "Consulte o HCM antes de registrar a proposta")
    fresh(row, hcm)
    catalog = hcm.catalog()
    sources = retrieve(db, row.request, catalog, row.id)
    if not payload.proposal.sources or not set(payload.proposal.sources) <= {s["id"] for s in sources}:
        raise HTTPException(422, "Use fontes presentes na análise")
    proposal = payload.proposal.model_dump()
    proposal["payloads"], proposal["assignments"] = checked_payloads(payload.proposal, row.snapshot, catalog, hcm)
    row.proposal = proposal; row.result = {}; row.status = "clarification" if payload.proposal.questions else "review"
    if row.workflow:
        row.workflow = {**row.workflow, "phase": "review", "manual_resolution": True}
    event(row, "manual_proposal", user, comment=payload.comment, proposal=proposal, sources=sources)
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/review")
def review(case_id: int, payload: Review, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision); editable(row)
    if payload.decision == "accept":
        if row.status != "review" or not row.proposal or row.proposal.get("questions"):
            raise HTTPException(409, "A proposta precisa estar completa e sem perguntas pendentes")
        fresh(row, hcm)
        row.status = "awaiting_execution"
    else:
        if not payload.comment.strip():
            raise HTTPException(422, "Explique a correção ou rejeição")
        row.status = "rejected" if payload.decision == "reject" else "clarification"
        row.result = {}
    event(row, payload.decision, user, comment=payload.comment)
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/check-result")
def check_result(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    if row.status not in ("awaiting_execution", "result_checked"):
        raise HTTPException(409, "Aceite a proposta antes de conferir a execução manual")
    current = hcm.snapshot(row.effective_from, row.company_code)
    catalog = hcm.catalog()
    if catalog["version"] != row.catalog_version or catalog.get("contract_hash", "") != (row.contract_hash or ""):
        raise HTTPException(409, "Catálogo mudou; abra uma nova análise")
    row.result = compare_result(row.snapshot, current, row.proposal["payloads"], catalog, row.proposal.get("assignments"))
    row.status = "result_checked"
    event(row, "result_checked", user, result=row.result)
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/validate")
def validate(case_id: int, payload: Finish, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    if row.status != "result_checked" or not row.result.get("configuration_matches") or not row.result.get("production_unchanged"):
        raise HTTPException(409, "Confira o resultado; configuração deve corresponder à proposta e produção deve estar preservada")
    if not payload.comment.strip() or not payload.evidence or not all(item.passed for item in payload.evidence):
        raise HTTPException(422, "Informe conclusão e evidências de testes aprovados")
    if {item.test for item in payload.evidence} != set(row.proposal["tests"]):
        raise HTTPException(422, "Registre evidência para cada teste da proposta")
    if fingerprint(hcm.snapshot(row.effective_from, row.company_code)) != fingerprint(row.result["snapshot"]):
        raise HTTPException(409, "O HCM mudou após a conferência; consulte o resultado novamente")
    row.status = "validated"
    event(row, "validated", user, comment=payload.comment, evidence=[item.model_dump() for item in payload.evidence], evidence_origin="declaração do especialista")
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/obsolete")
def obsolete(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user)):
    row = load(db, case_id, payload.revision)
    if row.status != "validated" or not payload.comment.strip():
        raise HTTPException(422, "Somente caso validado pode ser marcado obsoleto, com justificativa")
    row.status = "obsolete"; event(row, "obsolete", user, comment=payload.comment); save(db)
    return output(row)

from app import execution_api as _execution_api  # register routes without shadowing FastAPI app
from app import orchestration_api as _orchestration_api
from app import insights_api as _insights_api
