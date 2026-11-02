"""Deterministic checks between the model's proposal and the HCM contract v2. Nothing here decides HR
rules: fields, scopes, schemas and precedence come from the catalog and the snapshot."""
import hashlib
import json
from fastapi import HTTPException
from app.contracts import Proposal
from app.schema_tools import value_errors

NAMED_FIELDS = ("config_code", "name", "simulation_text")
LEGACY_KEYS = ("schedules", "as_of", "catalog_version", "erp_version", "groups", "company")
ASSIGNMENT_PROCESSES = ("vacations", "vacations_simulation")

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def fingerprint(snapshot):
    """Contract v2 snapshots carry their own fingerprint; v1 snapshots kept in old cases are recomputed."""
    return snapshot.get("fingerprint") or digest({key: snapshot.get(key) for key in LEGACY_KEYS})

def homologation_fingerprint(snapshot):
    """What must stay as tested before a result is approved: homologation rules and assignments, groups and company.
    Production is checked separately, because a human publication of the same rules is a legitimate later step."""
    return digest({"schedules": (snapshot.get("schedules") or {}).get("homologation"), "assignments": (snapshot.get("assignments") or {}).get("homologation"),
                   "groups": snapshot.get("groups"), "company": snapshot.get("company"), "as_of": snapshot.get("as_of"), "catalog_version": snapshot.get("catalog_version")})

def row_identity(row, fields):
    return json.dumps({"scope_key": row.get("scope_key"), "effective_from": row.get("effective_from"), **{name: row.get(name) for name in fields}, **{name: row.get(name) for name in NAMED_FIELDS if name in row}}, sort_keys=True)

def publication_consistent(initial, current, fields):
    """Production changed only by rows that exist, with the same values, in current homologation: the HCM publication flow."""
    before = {row_identity(row, fields) for row in initial["schedules"]["production"]}
    homologation = {row_identity(row, fields) for row in current["schedules"]["homologation"]}
    new_rows = [row for row in current["schedules"]["production"] if row_identity(row, fields) not in before]
    removed = before - {row_identity(row, fields) for row in current["schedules"]["production"]}
    assignments_ok = assignment_values(current, "production") in (assignment_values(initial, "production"), assignment_values(current, "homologation"))
    return bool(new_rows or removed) and not removed and all(row_identity(row, fields) in homologation for row in new_rows) and assignments_ok

def approval_hash(snapshot, payloads, assignments=None):
    return digest({"expected_fingerprint": fingerprint(snapshot), "configurations": payloads, "assignments": assignments})

def catalog_fields(catalog):
    return {field["id"]: field for field in catalog["fields"]}

def field_schema(field):
    """Catalog v2 publishes a JSON Schema per field; derive one for the few legacy catalogs still stored."""
    if field.get("schema"):
        return field["schema"]
    kind = field.get("type", "string")
    if kind == "integer":
        schema = {"type": "integer"}
        if field.get("minimum") is not None: schema["minimum"] = field["minimum"]
        if field.get("maximum") is not None: schema["maximum"] = field["maximum"]
        return schema
    return {"type": kind if kind in ("boolean", "string", "array", "object") else "string"}

def scope_types(catalog):
    return {scope["type"] for scope in catalog.get("scopes", [{"type": "company"}, {"type": "group"}])}

def scope_key(company_id, payload):
    if payload.get("config_code"):
        return f"company:{company_id}:config:{payload['config_code']}"
    if payload.get("group_id") is not None:
        return f"company:{company_id}:group:{payload['group_id']}"
    return f"company:{company_id}:default"

def case_catalog(row):
    """The catalog the case was analysed with: orchestrated cases keep it in workflow, legacy ones in the analysis event."""
    catalog = (row.workflow or {}).get("catalog") or {}
    if catalog.get("fields"):
        return catalog
    for event in reversed(row.events or []):
        for source in event.get("sources") or []:
            if str(source.get("id", "")).startswith("catalog:") and isinstance(source.get("content"), dict) and source["content"].get("fields"):
                return source["content"]
    return {"fields": [], "scopes": [{"type": "company"}, {"type": "group"}]}

def assignment_values(snapshot, environment="homologation"):
    current = (snapshot.get("assignments") or {}).get(environment) or {}
    return {process: current.get(process, []) for process in ASSIGNMENT_PROCESSES}

def checked_payloads(proposal: Proposal, snapshot, catalog, hcm):
    """Turns changes into complete HCM payloads, one per scope, and asks the official validator.
    Returns (payloads, assignments); assignments is None when the plan does not touch them."""
    if proposal.questions and proposal.changes:
        raise HTTPException(422, "Esclareça as perguntas antes de propor alterações")
    if not proposal.questions and not proposal.tests:
        raise HTTPException(422, "Informe pelo menos um teste para a proposta")
    fields = catalog_fields(catalog)
    scopes = scope_types(catalog)
    company_id = snapshot["company"]["id"]
    groups = {group["id"]: group for group in snapshot["groups"]}
    existing_named = {item["config_code"]: item for item in (snapshot.get("named_configurations") or {}).get("homologation", [])}
    new_named = {item.config_code: item for item in proposal.named_configurations}
    if (new_named or proposal.assignments is not None) and "config" not in scopes:
        raise HTTPException(422, "Este módulo não possui configurações nomeadas nem atribuições")
    for code in new_named:
        if code in existing_named:
            raise HTTPException(422, f"A configuração nomeada {code} já existe; altere-a pelo escopo config_code em vez de declará-la de novo")
    requested, seen = {}, set()
    for change in proposal.changes:
        kind, ident = change.scope()
        if kind not in scopes:
            raise HTTPException(422, "Escopo não disponível neste módulo")
        if kind == "group" and ident not in groups:
            raise HTTPException(422, "Parâmetro ou grupo inexistente/fora do escopo")
        if kind == "config" and ident not in existing_named and ident not in new_named:
            raise HTTPException(422, f"Configuração nomeada {ident} desconhecida: declare código e descrição em named_configurations")
        if change.parameter not in fields:
            raise HTTPException(422, "Parâmetro ou grupo inexistente/fora do escopo")
        errors = value_errors(change.value, field_schema(fields[change.parameter]), fields[change.parameter].get("label", change.parameter))
        if errors:
            raise HTTPException(422, {"message": "Valor fora do contrato do parâmetro", "errors": errors})
        if (kind, ident, change.parameter) in seen:
            raise HTTPException(422, "Parâmetro duplicado no mesmo escopo")
        seen.add((kind, ident, change.parameter))
        requested.setdefault((kind, ident), {})[change.parameter] = change.value
    for code in new_named:
        requested.setdefault(("config", code), {})
    effective = {row["group_id"]: row for row in snapshot["effective"]["homologation"]}
    general = (effective.get(None) or {}).get("configuration")
    grouped = {}
    def order(scope):
        kind, ident = scope
        return (0, 0, "") if kind == "company" else (1, ident, "") if kind == "group" else (2, 0, str(ident))
    for kind, ident in sorted(requested, key=order):
        exists = True
        if kind == "company":
            base = general
        elif kind == "group":
            row = effective.get(ident) or {}
            base = row.get("configuration")
            origin = (row.get("origin") or {}).get("scope") or ("group" if base and base.get("scope_key") == f"company:{company_id}:group:{ident}" else "company")
            if origin == "assignment":
                raise HTTPException(422, f"O grupo {row.get('group_name', ident)} recebe a regra da atribuição {row['origin'].get('label', '')}; altere a configuração nomeada ou as atribuições em vez da regra do grupo")
            exists = origin == "group"
            if not exists and ("company", None) in grouped:
                base = grouped[("company", None)]  # a new group override starts from the planned company rule, not a stale parent
        else:
            base = existing_named.get(ident)
            exists = base is not None
            if base is None:
                base = grouped.get(("company", None)) or general  # a new named configuration is a variant of the company rule
        if base is None and set(requested[(kind, ident)]) != set(fields):
            raise HTTPException(422, "Não há regra base: a configuração inicial precisa definir todos os parâmetros do catálogo")
        values = {name: base[name] for name in fields} if base else dict(requested[(kind, ident)])
        for parameter, value in requested[(kind, ident)].items():
            if exists and values[parameter] == value:
                raise HTTPException(422, "A proposta repete um valor já atendido nesse escopo")
            values[parameter] = value
        payload = {"company_id": company_id, "environment": "homologation", "group_id": ident if kind == "group" else None, "effective_from": snapshot["as_of"], **values}
        if "config" in scopes:
            named = new_named.get(ident) if kind == "config" else None
            source = named.model_dump() if named else existing_named.get(ident, {}) if kind == "config" else {}
            payload.update({field: source.get(field) for field in NAMED_FIELDS})
        grouped[(kind, ident)] = payload
    assignments = None
    if proposal.assignments is not None:
        names = {group["name"].lower() for group in groups.values()}
        codes = set(existing_named) | set(new_named)
        for process in ASSIGNMENT_PROCESSES:
            for item in getattr(proposal.assignments, process):
                if item.group.lower() not in names:
                    raise HTTPException(422, f"Atribuições: o grupo {item.group} não existe nesta empresa")
                if item.config not in codes:
                    raise HTTPException(422, f"Atribuições: a configuração {item.config} não existe nem foi declarada no plano")
        assignments = proposal.assignments.model_dump()
        if assignments == assignment_values(snapshot):
            raise HTTPException(422, "As atribuições propostas são iguais às atuais")
    payloads = list(grouped.values())
    if not payloads and assignments is None:
        return payloads, assignments
    report = hcm.validate(snapshot, payloads, assignments)
    if not report.get("valid"):
        raise HTTPException(422, {"message": "Validador oficial do HCM rejeitou a proposta", "errors": report.get("errors", [])})
    return payloads, assignments

def semantic_schedule(rows, fields, named=False):
    latest = {}
    for row in sorted(rows, key=lambda item: item["version"]):
        value = {"scope_key": row["scope_key"], "effective_from": row["effective_from"], **{name: row.get(name) for name in fields}}
        if named:
            value.update({name: row.get(name) for name in NAMED_FIELDS})
        latest[(row["scope_key"], row["effective_from"])] = value
    return latest

def compare_result(initial, current, payloads, catalog, assignments=None):
    fields = [field["id"] for field in catalog["fields"]]
    named = "config" in scope_types(catalog)
    expected = semantic_schedule(initial["schedules"]["homologation"], fields, named)
    for payload in payloads:
        scope = scope_key(payload["company_id"], payload)
        value = {"scope_key": scope, "effective_from": payload["effective_from"], **{name: payload.get(name) for name in fields}}
        if named:
            value.update({name: payload.get(name) for name in NAMED_FIELDS})
        expected[(scope, payload["effective_from"])] = value
    actual = semantic_schedule(current["schedules"]["homologation"], fields, named)
    production_unchanged = initial["schedules"]["production"] == current["schedules"]["production"] and \
        assignment_values(initial, "production") == assignment_values(current, "production")
    expected_assignments = assignments if assignments is not None else assignment_values(initial)
    assignments_match = assignment_values(current) == expected_assignments if (named or assignments is not None) else True
    differences = [{"scope": key[0], "effective_from": key[1], "expected": expected.get(key), "actual": actual.get(key)} for key in sorted(set(expected) | set(actual)) if expected.get(key) != actual.get(key)]
    production_published = (not production_unchanged) and publication_consistent(initial, current, fields)
    return {"configuration_matches": not differences and assignments_match, "assignments_match": assignments_match, "production_unchanged": production_unchanged,
            "production_published": production_published,
            "differences": differences, "consulted_at": current["consulted_at"],
            "evidence_origin": "API HCM; testes funcionais dependem de declaração do consultor", "snapshot": current}
