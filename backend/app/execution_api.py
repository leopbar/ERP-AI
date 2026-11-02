"""Durable orchestration. Only a human approval can start a homologation write."""
from uuid import uuid4
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import require_user
from app.contracts import Action, ApplyAction
from app.database import get_db
from app.hcm import get_hcm
from app.main import app, load, output, event, save, fresh
from app.workflow import approval_hash, case_catalog, fingerprint, compare_result, homologation_fingerprint, publication_consistent

def collect_result(row, operation):
    result = compare_result(row.snapshot, operation["after_snapshot"], row.proposal["payloads"], case_catalog(row), row.proposal.get("assignments"))
    result.update({"operation_id": operation["operation_id"], "automatic_tests": operation["report"], "execution": operation,
                   "evidence_origin": "Execução e testes automáticos registrados pelo HCM"})
    return result

@app.post("/api/cases/{case_id}/execute")
def execute(case_id: int, payload: ApplyAction, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    expected = approval_hash(row.snapshot, row.proposal["payloads"], row.proposal.get("assignments")) if row.snapshot and "payloads" in row.proposal else ""
    if not expected or payload.approval_hash != expected:
        raise HTTPException(409, "A aprovação não corresponde à proposta apresentada. Atualize o atendimento.")
    if row.status in ("executing", "execution_unknown"):
        packet = row.result["execution_request"]
    else:
        if row.status not in ("review", "awaiting_execution", "result_checked", "execution_blocked") or row.proposal.get("questions") or row.result.get("operation_id"):
            raise HTTPException(409, "Gere uma proposta completa para aplicar em homologação")
        fresh(row, hcm)
        packet = {"operation_id": str(uuid4()), "approved_by": user, "expected_fingerprint": fingerprint(row.snapshot),
                  "approval_hash": expected, "effective_from": row.effective_from, "company_code": row.company_code, "configurations": row.proposal["payloads"],
                  "assignments": row.proposal.get("assignments")}
        row.result = {"execution_request": packet}
        row.status = "executing"
        event(row, "execution_approved", user, approval_hash=expected, operation_id=packet["operation_id"], comment=payload.comment)
        save(db)
    try:
        operation = hcm.apply(packet)
    except HTTPException as exc:
        row.status = "execution_blocked" if exc.status_code in (403, 409, 422) else "execution_unknown"
        event(row, "execution_pending", user, operation_id=packet["operation_id"], error=exc.detail)
        save(db)
        raise
    row.result = collect_result(row, operation)
    row.result["execution_request"] = packet
    row.status = "applied" if operation["status"] == "applied" else "reverted" if operation["status"] == "reverted" else "execution_failed"
    event(row, "executed", user, operation_id=operation["operation_id"], status=operation["status"], report=operation["report"])
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/verify")
def verify(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    if row.status not in ("applied", "verification_failed") or not row.result.get("operation_id"):
        raise HTTPException(409, "Aplique a proposta antes de verificar")
    response = hcm.verify(row.result["operation_id"])
    operation = {**row.result["execution"], "after_snapshot": response["current_snapshot"], "report": response["report"]}
    result = collect_result(row, operation)
    result["execution_request"] = row.result["execution_request"]
    row.result = result
    report = response["report"]
    # A later publication of the same rules to production by the HCM flow is not a verification failure.
    only_production = all(check.get("passed") or str(check.get("name", "")).startswith("Produção") for check in report.get("checks", []))
    passed = bool(report["passed"] or (result.get("production_published") and only_production and result["configuration_matches"]))
    result["automatic_tests"] = {**report, "passed": passed, "hcm_passed": report["passed"], "production_published": bool(result.get("production_published"))}
    row.status = "applied" if passed else "verification_failed"
    event(row, "automatic_tests", user, report=report, production_published=result.get("production_published", False))
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/confirm-result")
def confirm(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    tests = row.result.get("automatic_tests") or {}
    if row.status != "applied" or not (tests.get("passed") or (row.result.get("production_published") and row.result.get("configuration_matches"))):
        raise HTTPException(409, "Os testes automáticos devem passar antes da aprovação do resultado")
    current = hcm.snapshot(row.effective_from, row.company_code)
    tested = row.result["snapshot"]
    if homologation_fingerprint(current) != homologation_fingerprint(tested):
        raise HTTPException(409, "A homologação mudou após os testes. Execute a verificação novamente.")
    published = False
    if current["schedules"]["production"] != tested["schedules"]["production"]:
        fields = [field["id"] for field in case_catalog(row)["fields"]]
        if not publication_consistent(tested, current, fields):
            raise HTTPException(409, "A produção mudou de forma diferente da homologação aplicada. Confira no HCM antes de aprovar.")
        published = True
        row.result = {**row.result, "production_published": True}
    row.status = "validated"
    event(row, "validated", user, comment=payload.comment or "Resultado conferido e aprovado pelo especialista.",
          evidence_origin="Testes automáticos do HCM e aprovação humana", report=row.result["automatic_tests"], production_published=published)
    save(db)
    return output(row)

@app.post("/api/cases/{case_id}/rollback")
def rollback(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    if row.status not in ("applied", "verification_failed", "validated", "rollback_unknown") or not row.result.get("operation_id"):
        raise HTTPException(409, "Não há aplicação automática disponível para reversão")
    if row.status != "rollback_unknown":
        row.result = {**row.result, "rollback_previous_status": row.status}
        row.status = "rollback_unknown"
        event(row, "rollback_approved", user, operation_id=row.result["operation_id"], comment=payload.comment)
        save(db)
    try:
        operation = hcm.rollback(row.result["operation_id"], fingerprint(row.result["execution"]["after_snapshot"]), payload.comment or "Reversão solicitada pelo consultor no ERP AI")
    except HTTPException as exc:
        if exc.status_code in (403, 409, 422):
            row.status = row.result.get("rollback_previous_status", "applied")
        event(row, "rollback_pending", user, error=exc.detail)
        save(db)
        raise
    row.status = "reverted"
    row.result = {**row.result, "rollback": operation["report"]["rollback"]}
    event(row, "reverted", user, operation_id=operation["operation_id"], comment=payload.comment, report=operation["report"]["rollback"])
    save(db)
    return output(row)
