"""Durable orchestration. Only a human approval can start a homologation write.
A plan may span modules: one HCM operation per module, applied in order. If a later module is refused or
reverted by the HCM, the modules already applied are rolled back, so a multi-module plan is all or nothing."""
from uuid import uuid4
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.auth import require_user
from app import progress
from app.contracts import Action, ApplyAction
from app.database import get_db
from app.hcm import get_hcm
from app.main import app, load, output, event, save, fresh
from app.workflow import plan_entries, plans_approval_hash, approval_hash, fingerprint, compare_result, homologation_fingerprint, publication_consistent

def client_for(hcm, module):
    return hcm.for_module(module) if hasattr(hcm, "for_module") and module else hcm

def module_operations(row):
    """Per-module operations of an applied case; cases applied before multi-module plans carry one implicit operation."""
    operations = row.result.get("operations")
    if operations is None and row.result.get("operation_id"):
        plans = plan_entries(row)
        plan = plans[0] if plans else {"module": row.module, "label": (row.workflow or {}).get("module_label") or row.module}
        operations = [{"module": plan["module"], "label": plan["label"], "operation_id": row.result["operation_id"], "execution": row.result["execution"],
                       "automatic_tests": row.result.get("automatic_tests"), "rolled_back": "rollback" in row.result}]
    return operations or []

def plan_for(row, operation):
    plans = plan_entries(row)
    return next((plan for plan in plans if plan["module"] == operation.get("module")), plans[0] if plans else None)

def collect_result(row, operations):
    """Compares each module's HCM state with its plan and aggregates: the whole plan matches only if every module matches."""
    items = []
    for operation in operations:
        plan = plan_for(row, operation)
        execution = operation["execution"]
        comparison = compare_result(plan["snapshot"], execution["after_snapshot"], plan["payloads"], plan["catalog"], plan.get("assignments"))
        tests = operation.get("automatic_tests") or execution["report"]
        items.append({**operation, **comparison, "automatic_tests": tests})
    multi = len(items) > 1
    checks = [{**check, "name": (item["label"] + ": " if multi else "") + str(check.get("name", ""))} for item in items for check in (item["automatic_tests"].get("checks") or [])]
    limits = [str(item["automatic_tests"]["limits"]) for item in items if item["automatic_tests"].get("limits")]
    tests = {"passed": all(item["automatic_tests"].get("passed") for item in items), "checks": checks, **({"limits": " ".join(limits)} if limits else {})}
    first = items[0]
    return {"operations": items, "modules": [item["label"] for item in items], "operation_id": first["operation_id"], "execution": first["execution"], "snapshot": first["snapshot"],
            "automatic_tests": tests, "configuration_matches": all(item["configuration_matches"] for item in items), "assignments_match": all(item["assignments_match"] for item in items),
            "production_unchanged": all(item["production_unchanged"] for item in items), "production_published": any(item["production_published"] for item in items),
            "differences": [{**difference, "module": item["label"]} for item in items for difference in item["differences"]], "consulted_at": first["consulted_at"],
            "compensation_failed": any(item.get("compensation_error") for item in items),
            "evidence_origin": "Execução e testes automáticos registrados pelo HCM"}

def undo(row, hcm, done, user, reason):
    """Compensation: roll back the modules already applied, newest first, so nothing of a failed plan stays in homologation."""
    for operation in reversed(done):
        if operation["execution"].get("status") != "applied":
            continue
        try:
            with progress.span("rollback", "hcm_rollback", module=operation["module"]):
                reverted = client_for(hcm, operation["module"]).rollback(operation["operation_id"], fingerprint(operation["execution"]["after_snapshot"]), reason)
            operation["execution"] = {**operation["execution"], **{key: reverted[key] for key in ("status", "report") if key in reverted}}
            operation["rolled_back"] = True
            event(row, "execution_compensated", user, operation_id=operation["operation_id"], module=operation["label"], reason=reason)
        except HTTPException as exc:
            operation["compensation_error"] = str(exc.detail)
            event(row, "compensation_failed", user, operation_id=operation["operation_id"], module=operation["label"], error=exc.detail)

@app.post("/api/cases/{case_id}/execute")
@progress.tracked
def execute(case_id: int, payload: ApplyAction, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    plans = plan_entries(row)
    expected = plans_approval_hash(plans)
    if not expected or payload.approval_hash != expected:
        raise HTTPException(409, "A aprovação não corresponde à proposta apresentada. Atualize o atendimento.")
    if row.status in ("executing", "execution_unknown"):
        request = row.result["execution_request"]
        packets = request.get("packets") or [request]
    else:
        if row.status not in ("review", "awaiting_execution", "result_checked", "execution_blocked") or row.proposal.get("questions") or row.result.get("operation_id"):
            raise HTTPException(409, "Gere uma proposta completa para aplicar em homologação")
        with progress.span("apply", "context_check"):
            fresh(row, hcm)
        # The HCM recomputes each operation's hash from its own packet; the plan-level hash the user approved covers all of them.
        packets = [{"operation_id": str(uuid4()), "approved_by": user, "expected_fingerprint": fingerprint(plan["snapshot"]),
                    "approval_hash": approval_hash(plan["snapshot"], plan["payloads"], plan.get("assignments")),
                    "effective_from": row.effective_from, "company_code": row.company_code, "configurations": plan["payloads"], "assignments": plan.get("assignments"),
                    "module": plan["module"], "label": plan["label"]} for plan in plans]
        request = {"approval_hash": expected, "approved_by": user, "packets": packets}
        row.result = {"execution_request": request}
        row.status = "executing"
        event(row, "execution_approved", user, approval_hash=expected, operation_id=packets[0]["operation_id"], operation_ids=[p["operation_id"] for p in packets],
              modules=[p["label"] for p in packets], comment=payload.comment)
        save(db)
    done = []
    for packet in packets:
        module = packet.get("module") or (plans[0]["module"] if plans else row.module)
        label = packet.get("label") or (plans[0]["label"] if plans else module)
        try:
            with progress.span("apply", "hcm_apply", module=module):
                operation = client_for(hcm, module).apply({key: value for key, value in packet.items() if key not in ("module", "label")})
        except HTTPException as exc:
            if done:
                undo(row, hcm, done, user, f"Falha na aplicação de {label}: {exc.detail}")
                row.result = {**collect_result(row, done), "execution_request": request}
                row.status = "execution_failed"
                event(row, "execution_failed", user, module=label, error=exc.detail, undone=[item["label"] for item in done if item.get("rolled_back")])
                save(db)
                raise HTTPException(exc.status_code, f"{label}: {exc.detail} As alterações já aplicadas nos outros módulos foram desfeitas.") from exc
            row.status = "execution_blocked" if exc.status_code in (403, 409, 422) else "execution_unknown"
            event(row, "execution_pending", user, operation_id=packet["operation_id"], module=label, error=exc.detail)
            save(db)
            raise
        done.append({"module": module, "label": label, "operation_id": operation["operation_id"], "execution": operation})
        if operation["status"] != "applied":
            # The HCM reverted this module itself (its tests failed): the modules applied before it must not stay.
            undo(row, hcm, done[:-1], user, f"O HCM desfez a aplicação de {label}; o plano é único.")
            break
    with progress.span("tests", "result_compare"):
        row.result = {**collect_result(row, done), "execution_request": request}
    progress.emit("tests", "done" if row.result["automatic_tests"]["passed"] else "error")
    statuses = {item["execution"]["status"] for item in done}
    complete = len(done) == len(packets)
    row.status = "applied" if complete and statuses == {"applied"} else "reverted" if complete and len(done) == 1 and statuses == {"reverted"} else "execution_failed"
    event(row, "executed", user, operation_id=done[0]["operation_id"], operations=[{"module": item["label"], "operation_id": item["operation_id"], "status": item["execution"]["status"]} for item in done],
          status=row.status, report=row.result["automatic_tests"])
    save(db)
    if row.status == "applied":
        progress.emit("experience", "waiting")
    return output(row)

@app.post("/api/cases/{case_id}/verify")
@progress.tracked
def verify(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    operations = module_operations(row)
    if row.status not in ("applied", "verification_failed") or not operations:
        raise HTTPException(409, "Aplique a proposta antes de verificar")
    refreshed = []
    progress.emit("tests")
    for operation in operations:
        with progress.span("tests", "hcm_verify", module=operation["module"]):
            response = client_for(hcm, operation["module"]).verify(operation["operation_id"])
        refreshed.append({**operation, "execution": {**operation["execution"], "after_snapshot": response["current_snapshot"], "report": response["report"]}, "automatic_tests": None})
    with progress.span("tests", "result_compare"):
        result = collect_result(row, refreshed)
    hcm_passed = True
    for item in result["operations"]:
        report = item["automatic_tests"]
        # A later publication of the same rules to production by the HCM flow is not a verification failure.
        only_production = all(check.get("passed") or str(check.get("name", "")).startswith("Produção") for check in report.get("checks", []))
        item["passed"] = bool(report["passed"] or (item["production_published"] and only_production and item["configuration_matches"]))
        hcm_passed = hcm_passed and bool(report["passed"])
    passed = all(item["passed"] for item in result["operations"])
    result["automatic_tests"] = {**result["automatic_tests"], "passed": passed, "hcm_passed": hcm_passed, "production_published": bool(result["production_published"])}
    result["execution_request"] = row.result["execution_request"]
    row.result = result
    row.status = "applied" if passed else "verification_failed"
    event(row, "automatic_tests", user, report=result["automatic_tests"], production_published=result["production_published"])
    save(db)
    progress.emit("tests", "done" if passed else "error")
    return output(row)

@app.post("/api/cases/{case_id}/confirm-result")
@progress.tracked
def confirm(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    tests = row.result.get("automatic_tests") or {}
    if row.status != "applied" or not (tests.get("passed") or (row.result.get("production_published") and row.result.get("configuration_matches"))):
        raise HTTPException(409, "Os testes automáticos devem passar antes da aprovação do resultado")
    operations = module_operations(row)
    progress.emit("experience")
    multi = len(operations) > 1
    published = False
    for operation in operations:
        prefix = operation["label"] + ": " if multi else ""
        with progress.span("experience", "context_check", module=operation["module"]):
            current = client_for(hcm, operation["module"]).snapshot(row.effective_from, row.company_code)
        tested = operation["execution"]["after_snapshot"]
        if homologation_fingerprint(current) != homologation_fingerprint(tested):
            raise HTTPException(409, prefix + "A homologação mudou após os testes. Execute a verificação novamente.")
        if current["schedules"]["production"] != tested["schedules"]["production"]:
            fields = [field["id"] for field in plan_for(row, operation)["catalog"]["fields"]]
            if not publication_consistent(tested, current, fields):
                raise HTTPException(409, prefix + "A produção mudou de forma diferente da homologação aplicada. Confira no HCM antes de aprovar.")
            published = True
    if published:
        row.result = {**row.result, "production_published": True}
    row.status = "validated"
    event(row, "validated", user, comment=payload.comment or "Resultado conferido e aprovado pelo especialista.",
          evidence_origin="Testes automáticos do HCM e aprovação humana", report=row.result["automatic_tests"], production_published=published)
    save(db)
    progress.emit("experience", "done")
    return output(row)

@app.post("/api/cases/{case_id}/rollback")
@progress.tracked
def rollback(case_id: int, payload: Action, db: Session = Depends(get_db), user=Depends(require_user), hcm=Depends(get_hcm)):
    row = load(db, case_id, payload.revision)
    operations = [dict(operation) for operation in module_operations(row)]
    if row.status not in ("applied", "verification_failed", "validated", "rollback_unknown") or not operations:
        raise HTTPException(409, "Não há aplicação automática disponível para reversão")
    if row.status != "rollback_unknown":
        row.result = {**row.result, "rollback_previous_status": row.status}
        row.status = "rollback_unknown"
        event(row, "rollback_approved", user, operation_id=operations[0]["operation_id"], operation_ids=[item["operation_id"] for item in operations], comment=payload.comment)
        save(db)
    reason = payload.comment or "Reversão solicitada pelo consultor no ERP AI"
    progress.emit("rollback")
    failure = None
    for operation in reversed(operations):
        if operation.get("rolled_back"):
            continue
        try:
            with progress.span("rollback", "hcm_rollback", module=operation["module"]):
                reverted = client_for(hcm, operation["module"]).rollback(operation["operation_id"], fingerprint(operation["execution"]["after_snapshot"]), reason)
        except HTTPException as exc:
            failure = exc
            event(row, "rollback_pending", user, operation_id=operation["operation_id"], module=operation["label"], error=exc.detail)
            break
        operation["rolled_back"] = True
        operation["rollback"] = reverted["report"]["rollback"]
        event(row, "reverted", user, operation_id=operation["operation_id"], module=operation["label"], comment=payload.comment, report=reverted["report"]["rollback"])
    row.result = {**row.result, "operations": operations}
    if failure is not None:
        if failure.status_code in (403, 409, 422):
            row.status = row.result.get("rollback_previous_status", "applied")
        save(db)
        prefix = operation["label"] + ": " if len(operations) > 1 else ""
        raise HTTPException(failure.status_code, prefix + str(failure.detail))
    row.status = "reverted"
    row.result = {**row.result, "rollback": operations[0]["rollback"]}
    save(db)
    progress.emit("rollback", "done")
    return output(row)
