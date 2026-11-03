"""Authenticated UI actions on the durable LangGraph workflow."""
import logging
from uuid import uuid4
from typing import Literal
from fastapi import Depends, HTTPException, Query, Response
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from langgraph.types import Command
from app.auth import require_user
from app.contracts import Action
from app.database import get_db
from app.hcm import get_hcm
from app.main import app, editable, event, load, output, save
from app.models import Case, InterpretationMemory
from app.orchestration import EXAMPLES, GENERIC_EXAMPLE, module_label, build_graph, checkpointer, validate_models, record_memory
from app.hcm import canonical_module
from app.knowledge import record_lesson
from app.workflow_map import stage_map
from app import progress

class Start(Action):
    primary_model: str = "gpt-6-luna"
    escalation_model: str = "gpt-6.1-sol"
    request: str | None = Field(default=None, min_length=10, max_length=8000)

class Continue(Action):
    action: Literal["guide", "clarify", "handoff", "choose", "retry", "expert", "close"]
    module: str = Field(default="vacations", max_length=40)
    request: str = Field(default="", max_length=8000)
    # accept: the suggestion as shown, with readings naming the reading picked for each doubtful item (index, 0 = the suggested one).
    choice: Literal["accept", "none"] = "none"
    readings: dict[str, int] = Field(default_factory=dict, max_length=20)
    company_code: str = Field(default="", max_length=20)

def locked(db, case_id, revision):
    # Concurrent resume requests must check revision after acquiring the row lock.
    row = db.scalar(select(Case).where(Case.id == case_id).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Atendimento não encontrado")
    if row.revision != revision:
        raise HTTPException(409, "Atendimento mudou. Atualize antes de continuar.")
    editable(row)
    return row

def project(db, row, graph, config, user, error=None):
    checkpoint = graph.get_state(config)
    state = checkpoint.values or {}
    pending = next((i.value for task in checkpoint.tasks for i in task.interrupts), None)
    phase = pending.get("phase") if pending else state.get("phase", "starting")
    previous = row.workflow or {}
    scope = [canonical_module(state["module"])] if state.get("module") else []
    scope += [canonical_module(m) for m in state.get("modules_in_scope", []) if canonical_module(m) not in scope]
    labels = [module_label(state, module) for module in scope]
    catalogs = state.get("catalogs") or ({scope[0]: state["catalog"]} if scope and state.get("catalog") else {})
    data = {**previous, "engine": "LangGraph", "phase": phase, "module": state.get("module"),
        "module_label": " + ".join(labels) if labels else "Identificando módulo", "modules": state.get("modules", []),
        "modules_in_scope": scope, "module_labels": labels, "extensions": state.get("extensions", []),
        "catalogs": {module: {"module_label": module_label(state, module), "version": catalog.get("version", ""), "fields": catalog.get("fields", [])} for module, catalog in catalogs.items()},
        "example": EXAMPLES.get(state.get("module"), GENERIC_EXAMPLE), "classification": state.get("classification", {}),
        "capabilities": state.get("handoff_capabilities", []),
        "handoff_summary": state.get("handoff_summary", {}),
        "catalog_fields": [field for catalog in catalogs.values() for field in catalog.get("fields", [])],
        "catalog": state.get("catalog", {}),
        "scope_reason": state.get("scope_reason", ""), "scope_rejection_count": state.get("scope_rejection_count", 0),
        "clarification_count": state.get("clarification_count", 0),
        "company_query": state.get("company_query", ""), "company_matches": state.get("company_matches", {}),
        "confirmed_company": state.get("confirmed_company", {}),
        "request": state.get("request", row.request), "attempt": min(state.get("attempt", 1), 2),
        "requirements": state.get("requirements", []), "information_gaps": state.get("information_gaps", []),
        "suggestion": state.get("suggestion", {}), "not_configured": (state.get("selected") or {}).get("not_configured", []),
        "options": state.get("options", []), "explanation": state.get("explanation", ""),
        "selected": state.get("selected", {}), "confirmed_meaning": state.get("confirmed_meaning", ""),
        "memories": state.get("memories", []), "metrics": state.get("metrics", []),
        "reason": (pending or {}).get("reason", state.get("handoff_reason", "")),
        "category": state.get("handoff_category", "interpretation_review"),
        "resolution": state.get("resolution", ""), "error": error, "next_nodes": list(checkpoint.next)}
    row.workflow = data
    if phase == "out_of_scope" and (previous.get("phase") != phase or previous.get("scope_rejection_count") != data["scope_rejection_count"]):
        event(row, "scope_rejected", user, reason=data["scope_reason"], count=data["scope_rejection_count"])
    if data["confirmed_company"]:
        row.company_code = data["confirmed_company"]["code"]
    if data["confirmed_company"] and data["confirmed_company"] != previous.get("confirmed_company"):
        event(row, "company_identified", user, company=data["confirmed_company"])
    if state.get("module"):
        row.module = canonical_module(state["module"])
    if "snapshot" in state:
        row.snapshot = state["snapshot"]
        row.catalog_version = state["catalog"].get("version", "")
        row.contract_hash = state["catalog"].get("contract_hash", "")
    if "proposal" in state:
        row.proposal = state["proposal"]
    record_memory(db, row, state)
    if error:
        row.status = "workflow_error"
    else:
        row.status = {"guide": "awaiting_context", "clarify": "awaiting_context", "choice": "awaiting_interpretation", "consultant": "consultant_queue",
                      "review": "review", "closed": "consultant_closed", "out_of_scope": "out_of_scope"}.get(phase, "workflow_error")
    old_count = len(previous.get("metrics", []))
    for metrics in data["metrics"][old_count:]:
        event(row, "agent_completed", user, role=metrics["role"], metrics=metrics)
    for note in state.get("repair_notes", [])[len(previous.get("repair_notes", [])):]:
        record_lesson(db, row, "validator_rejection", "Pedido: " + row.request[:300] + " | Rejeição do validador: " + str(note))
    data["repair_notes"] = state.get("repair_notes", [])
    data["redirects"] = state.get("redirects", [])
    if state.get("redirects") and len(state["redirects"]) > len(previous.get("redirects", [])):
        event(row, "module_redirected", user, from_module=state["redirects"][-1], to_module=state.get("module"), reason=(state.get("classification") or {}).get("redirect_reason", ""))
    if state.get("extensions") and len(state["extensions"]) > len(previous.get("extensions", [])):
        added = state["extensions"][len(previous.get("extensions", [])):]
        event(row, "module_extended", user, modules=added, labels=[module_label(state, module) for module in added], scope=scope,
              reason=(state.get("classification") or {}).get("extension_reason", ""))
    if phase == "consultant" and previous.get("phase") != "consultant" and data["category"] in ("validation_failed", "plan_questions", "plan_incomplete"):
        record_lesson(db, row, data["category"], "Pedido: " + row.request[:300] + " | " + str(data["reason"]))
    if phase == "review" and previous.get("phase") != "review":
        event(row, "analyzed", user, sources=state.get("sources", []), proposal=row.proposal, snapshot=row.snapshot)
    if phase == "consultant" and previous.get("phase") != "consultant":
        event(row, "consultant_requested", user, comment=data["reason"])
    save(db)

def drive(db, row, user, hcm, value):
    with progress.observe(db.get_bind(), row.id, row.revision, flow_id=row.workflow.get("thread_id")):
        return drive_observed(db, row, user, hcm, value)


def drive_observed(db, row, user, hcm, value):
    with checkpointer() as saver:
        graph = build_graph(db, hcm, saver)
        config = {"configurable": {"thread_id": row.workflow["thread_id"]}, "recursion_limit": 40}
        try:
            graph.invoke(value, config=config)
        except HTTPException as exc:
            project(db, row, graph, config, user, str(exc.detail))
            raise
        except Exception as exc:
            # Never expose a provider request, key, database URL or traceback in the response; keep it in the server log.
            logging.getLogger("erp_ai.workflow").exception("Workflow failure on case %s", row.id)
            message = "O fluxo foi interrompido. Use Retomar fluxo; o HCM não foi alterado por estes agentes."
            project(db, row, graph, config, user, message)
            raise HTTPException(502, message) from exc
        project(db, row, graph, config, user)
        if row.status == "review":
            progress.emit("approval", "waiting")
    return output(row)


@app.get("/api/cases/{case_id}/progress")
def case_progress(case_id: int, response: Response, db: Session = Depends(get_db), user=Depends(require_user)):
    row = load(db, case_id)
    response.headers["Cache-Control"] = "no-store"
    # Same access policy as reading the case; no HCM/model calls, no row locks.
    return {"case_id": row.id, "revision": row.revision, "status": row.status,
            "phase": (row.workflow or {}).get("phase"), "progress": progress.read(db.get_bind(), case_id)}

@app.get("/api/workflow/map")
def journey_map(user=Depends(require_user)):
    # The ERP's own stage map: no case, HCM or model data.
    return stage_map()

@app.post("/api/cases/{case_id}/orchestrate")
def start(case_id: int, payload: Start, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = locked(db, case_id, payload.revision)
    validate_models(payload.primary_model, payload.escalation_model)
    if row.workflow and row.workflow.get("phase") not in ("review", "closed", "guide", "clarify", "choice", "consultant", "out_of_scope"):
        raise HTTPException(409, "Há um fluxo em andamento. Retome o atendimento atual.")
    if payload.request is not None:
        replacement = payload.request.strip()
        if len(replacement) < 10:
            raise HTTPException(422, "Descreva o novo pedido com ao menos 10 caracteres.")
        event(row, "request_rephrased", user, previous_request=row.request, request=replacement)
        row.request = replacement
    # Persist identity before invoking the graph: recovery uses the same checkpoint.
    state = {"case_id": row.id, "actor": user, "company": row.company_code, "request": row.request + ("\nEsclarecimento: " + payload.comment.strip() if payload.comment.strip() else ""),
        "original_request": row.request, "effective_from": row.effective_from, "attempt": 1,
        "latest_input": payload.comment.strip() or row.request, "scope_rejection_count": (row.workflow or {}).get("scope_rejection_count", 0),
        "primary_model": payload.primary_model, "escalation_model": payload.escalation_model,
        "metrics": [], "rejected": [], "phase": "starting"}
    row.workflow = {"thread_id": str(uuid4()), "phase": "starting", "engine": "LangGraph",
                    "primary_model": payload.primary_model, "escalation_model": payload.escalation_model, "initial_state": state}
    row.proposal = {}; row.result = {}; row.snapshot = {}; row.catalog_version = ""; row.status = "analyzing"
    for memory in db.scalars(select(InterpretationMemory).where(InterpretationMemory.case_id == row.id)):
        memory.active = False
    event(row, "workflow_started", user, primary_model=payload.primary_model, escalation_model=payload.escalation_model)
    save(db)
    # Hold a lock during the model call, also protecting against duplicate resume.
    row = locked(db, case_id, row.revision)
    return drive(db, row, user, hcm, state)

@app.post("/api/cases/{case_id}/workflow")
def resume(case_id: int, payload: Continue, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = locked(db, case_id, payload.revision)
    if not row.workflow.get("thread_id"):
        raise HTTPException(409, "Inicie o fluxo de agentes primeiro.")
    if row.workflow.get("initial_state", {}).get("actor") != user:
        raise HTTPException(403, "Este atendimento pertence a outro consultor.")
    phase = row.workflow.get("phase")
    if payload.action == "retry":
        if row.status not in ("workflow_error", "analyzing"):
            raise HTTPException(409, "Não há etapa com erro para retomar.")
        # Recover from the checkpoint. If failure preceded its first write, replay input.
        with checkpointer() as saver:
            graph = build_graph(db, hcm, saver)
            config = {"configurable": {"thread_id": row.workflow["thread_id"]}}
            checkpoint = graph.get_state(config)
            if any(task.interrupts for task in checkpoint.tasks):
                project(db, row, graph, config, user)
                return output(row)
            value = None if checkpoint.values else row.workflow["initial_state"]
        return drive(db, row, user, hcm, value)
    if row.status == "workflow_error":
        raise HTTPException(409, "Retome o fluxo antes de responder novamente.")
    if payload.action in ("clarify", "handoff") and phase == "clarify":
        if payload.action == "clarify" and len(payload.comment.strip()) < 3:
            raise HTTPException(422, "Informe o esclarecimento para refazer a identificação.")
        value = {"comment": payload.comment.strip(), "action": payload.action}
    elif payload.action == "guide" and phase == "guide":
        if payload.request and len(payload.request.strip()) < 10:
            raise HTTPException(422, "Descreva a necessidade com ao menos 10 caracteres.")
        if not payload.company_code:
            raise HTTPException(422, "Busque a empresa pelo nome e confirme qual cadastro deve ser atualizado.")
        matches = hcm.companies(payload.company_code)["items"]
        company = next((c for c in matches if c["code"] == payload.company_code), None)
        if not company:
            raise HTTPException(422, "Empresa não encontrada no HCM. Faça a busca pelo nome novamente.")
        event(row, "company_confirmed", user, company=company)
        value = {"module": canonical_module(payload.module), "request": payload.request, "confirmed_company": company}
    elif payload.action == "choose" and phase == "choice":
        if not row.workflow.get("suggestion"):
            # Paused on the three options of an earlier version: a new analysis builds the suggestion.
            raise HTTPException(409, "Este atendimento foi analisado no formato anterior. Use Reanalisar pedido.")
        value = {"choice": payload.choice, "readings": payload.readings, "comment": payload.comment.strip()}
        event(row, "interpretation_rejected" if payload.choice == "none" else "interpretation_confirmed", user,
              choice=payload.choice, readings=payload.readings, attempt=row.workflow["attempt"], comment=payload.comment)
        if payload.choice == "none" and payload.comment.strip():
            record_lesson(db, row, "interpretation_rejected", "Sugestão recusada para o pedido: " + row.request[:300] + " | Correção: " + payload.comment.strip())
    elif payload.action in ("expert", "close") and phase == "consultant":
        if len(payload.comment.strip()) < 10:
            raise HTTPException(422, "O consultor deve registrar sua interpretação ou conclusão com ao menos 10 caracteres.")
        if payload.action == "expert" and not row.workflow.get("catalog", {}).get("fields"):
            raise HTTPException(409, "Este módulo ainda não possui conector de configuração. Registre a conclusão e encerre o encaminhamento.")
        value = {"action": payload.action, "comment": payload.comment.strip()}
        event(row, "consultant_resolution", user, comment=payload.comment, decision=payload.action)
        record_lesson(db, row, "consultant_resolution", "Pedido: " + row.request[:300] + " | Conclusão do consultor: " + payload.comment.strip())
    else:
        raise HTTPException(409, "Esta ação não corresponde à etapa atual. Atualize o atendimento.")
    return drive(db, row, user, hcm, Command(resume=value))

@app.get("/api/companies")
def company_search(query: str = Query(default="", max_length=180), user=Depends(require_user), hcm=Depends(get_hcm)):
    return hcm.companies(query)

@app.get("/api/consultant-queue")
@app.get("/api/unhandled")
def queue(db: Session = Depends(get_db), user=Depends(require_user)):
    statuses = ("consultant_queue", "proposal_failed", "execution_failed", "execution_blocked", "workflow_error", "verification_failed")
    rows = db.scalars(select(Case).where(Case.status.in_(statuses)).order_by(Case.id)).all()
    categories = {"proposal_failed": "validation_failed", "execution_failed": "execution_failed", "execution_blocked": "execution_blocked", "workflow_error": "technical_error", "verification_failed": "verification_failed"}
    return [{"id": r.id, "request": r.request, "company": r.workflow.get("confirmed_company", {}).get("name") or r.snapshot.get("company", {}).get("name"), "module": r.workflow.get("module_label"), "status": r.status,
             "category": categories.get(r.status, r.workflow.get("category", "interpretation_review")),
             "reason": r.workflow.get("error") or r.workflow.get("reason") or next((str(e.get("error")) for e in reversed(r.events) if e.get("error")), "O atendimento precisa de revisão do consultor."), "updated_at": r.updated_at} for r in rows]

@app.get("/api/interpretation-memories")
def memories(db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    rows = db.scalars(select(InterpretationMemory).where(InterpretationMemory.actor == user).order_by(InterpretationMemory.id.desc()).limit(100)).all()
    try:
        labels = {canonical_module(item["id"]): item["label"] for item in hcm.modules()}
    except HTTPException:
        labels = {}
    return [{"id": r.id, "case_id": r.case_id, "request": r.request, "meaning": r.meaning, "module": labels.get(canonical_module(r.module), r.module), "active": r.active} for r in rows]

@app.post("/api/interpretation-memories/{memory_id}/forget")
def forget(memory_id: int, db: Session = Depends(get_db), user=Depends(require_user)):
    memory = db.get(InterpretationMemory, memory_id)
    if memory is None or memory.actor != user:
        raise HTTPException(404, "Memória não encontrada")
    memory.active = False
    save(db)
    return {"ok": True}
