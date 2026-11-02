"""Observability and learning controls: aggregated metrics, structured capability gaps and the
knowledge switches. Read-only over the case ledger, except for the switches."""
import csv
import io
import unicodedata
from collections import defaultdict
from fastapi import Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.auth import require_user
from app.database import get_db
from app.knowledge import DEFAULT_SETTINGS, knowledge_settings, save_knowledge_settings
from app.main import app, save
from app.models import Case

class KnowledgeSettingsInput(BaseModel):
    cross_company: bool = DEFAULT_SETTINGS["cross_company"]
    lessons: bool = DEFAULT_SETTINGS["lessons"]

@app.get("/api/knowledge/settings")
def get_knowledge_settings(db: Session = Depends(get_db), user=Depends(require_user)):
    return knowledge_settings(db)

@app.post("/api/knowledge/settings")
def set_knowledge_settings(payload: KnowledgeSettingsInput, db: Session = Depends(get_db), user=Depends(require_user)):
    values = save_knowledge_settings(db, payload.model_dump())
    save(db)
    return values

def case_module(row):
    return row.module or (row.workflow or {}).get("module") or "indefinido"

@app.get("/api/metrics")
def metrics(db: Session = Depends(get_db), user=Depends(require_user)):
    """Per model and role: calls, repairs, tokens, duration and estimated cost. Per module: funnel counts.
    Estimates come from the usage each call reported; they are not an invoice."""
    rows = db.scalars(select(Case)).all()
    by_model = defaultdict(lambda: {"calls": 0, "repairs": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0, "cost_usd": 0.0})
    by_module = defaultdict(lambda: {"cases": 0, "first_round_accepted": 0, "escalations": 0, "consultant": 0, "applied": 0, "reverted": 0, "validated": 0, "out_of_scope": 0})
    total_cost, validated = 0.0, 0
    for row in rows:
        module = by_module[case_module(row)]
        module["cases"] += 1
        kinds = [event.get("type") for event in row.events or []]
        if any(event.get("type") == "interpretation_confirmed" and event.get("attempt", 1) == 1 for event in row.events or []):
            module["first_round_accepted"] += 1
        if any(event.get("type") == "interpretation_rejected" and event.get("attempt", 1) == 1 for event in row.events or []):
            module["escalations"] += 1
        module["consultant"] += "consultant_requested" in kinds
        module["applied"] += "executed" in kinds
        module["reverted"] += "reverted" in kinds
        module["validated"] += "validated" in kinds
        module["out_of_scope"] += "scope_rejected" in kinds
        validated += row.status == "validated"
        for event in row.events or []:
            data = event.get("metrics") or {}
            if event.get("type") not in ("agent_completed", "analyzed", "proposal_failed") or data.get("mode") != "model":
                continue
            key = (data.get("model", "?"), data.get("role") or event.get("role") or "configuration_agent")
            item = by_model[key]
            item["calls"] += 1
            item["repairs"] += bool(data.get("repair"))
            usage = data.get("usage") or {}
            item["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            item["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            item["seconds"] += float(data.get("duration_seconds") or 0)
            cost = float((data.get("cost") or {}).get("estimated_usd") or 0)
            item["cost_usd"] += cost
            total_cost += cost
    return {"by_model": [{"model": model, "role": role, **item, "average_seconds": round(item["seconds"] / item["calls"], 2) if item["calls"] else 0, "cost_usd": round(item["cost_usd"], 6)}
                         for (model, role), item in sorted(by_model.items())],
            "by_module": [{"module": module, **item} for module, item in sorted(by_module.items())],
            "totals": {"cases": len(rows), "validated": validated, "cost_usd": round(total_cost, 6), "cost_per_validated_case": round(total_cost / validated, 6) if validated else None},
            "basis": "Consumo reportado por chamada e preços do catálogo de modelos; não é fatura."}

def normalized(text):
    return " ".join(unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode().split())

@app.get("/api/unhandled/capabilities")
def capability_gaps(format: str = Query(default="json", pattern="^(json|csv)$"), db: Session = Depends(get_db), user=Depends(require_user)):
    """Missing capabilities the interpreter described, grouped by module and description, with the cases that raised them."""
    rows = db.scalars(select(Case).where(Case.status.in_(("consultant_queue", "consultant_closed")))).all()
    groups = {}
    for row in rows:
        for item in (row.workflow or {}).get("capabilities") or []:
            key = (item.get("module", ""), normalized(item.get("description", "")))
            group = groups.setdefault(key, {"module": item.get("module", ""), "description": item.get("description", ""), "count": 0, "open": 0, "case_ids": []})
            group["count"] += 1
            group["open"] += row.status == "consultant_queue"
            group["case_ids"].append(row.id)
    items = sorted(groups.values(), key=lambda g: (-g["count"], g["module"], g["description"]))
    if format == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=";")
        writer.writerow(["modulo", "descricao", "atendimentos", "em_aberto", "casos"])
        for group in items:
            writer.writerow([group["module"], group["description"], group["count"], group["open"], " ".join(str(i) for i in group["case_ids"])])
        return Response(content=buffer.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=capacidades-ausentes.csv"})
    return items
