import re
import unicodedata
from sqlalchemy import select
from app.models import Case

def terms(text):
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return set(re.findall(r"[a-z0-9]{3,}", plain)) - {"para", "uma", "com", "dos", "das", "que", "por"}

def retrieve(db, request, catalog, exclude_id=None, company_code=None):
    """Official catalog plus validated cases of the same company, module and contract. A different
    contract hash means the HCM changed what the fields and precedence mean, so older cases stay out."""
    sources = [{"id": "catalog:" + catalog["version"], "title": "Catálogo oficial de " + catalog.get("module_label", "Férias") + " do HCM", "content": catalog}]
    if company_code is None and exclude_id is not None:
        case = db.get(Case, exclude_id)
        company_code = case.company_code if case else None
    rows = db.scalars(select(Case).where(Case.status == "validated", Case.company_code == company_code, Case.catalog_version == catalog["version"],
                                         Case.contract_hash == catalog.get("contract_hash", ""), Case.module == catalog.get("module", "vacations"))).all()
    ranked = []
    for row in rows:
        if row.id == exclude_id:
            continue
        score = len(terms(request) & terms(row.request + " " + row.proposal.get("summary", "")))
        if score:
            ranked.append((score, row))
    for _, row in sorted(ranked, key=lambda x: (x[0], x[1].id), reverse=True)[:4]:
        sources.append({"id": f"case:{row.id}", "title": f"Caso validado #{row.id}", "content": {"request": row.request, "proposal": row.proposal, "validation": row.events[-1].get("comment", "")}})
    return sources
