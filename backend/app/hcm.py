"""HCM integration client for contract v2. Module names, fields, scopes and precedence come from the
HCM catalog; the ERP keeps only the alias table and the HTTP plumbing."""
import os
import time
import httpx
from fastapi import HTTPException, Depends, Request
from app.database import get_db

CONTRACT = 2
ALIASES = {"time": "timekeeping"}
_modules_cache = {"at": 0.0, "items": None}

def canonical_module(module):
    """Single place where request-side aliases become HCM module identifiers."""
    module = (module or "vacations").strip()
    return ALIASES.get(module, module)

class HCMClient:
    def __init__(self, module="vacations"):
        self.base = os.getenv("HCM_BASE_URL", "http://localhost:8000").rstrip("/") + f"/api/integration/v{CONTRACT}"
        self.module = canonical_module(module)
        self.path = "/vacations" if self.module == "vacations" else "/modules/" + self.module

    def for_module(self, module):
        return HCMClient(module)

    def call(self, path: str, *, params=None, payload=None, execution=False):
        token = os.getenv("HCM_INTEGRATION_TOKEN", "")
        if not token:
            raise HTTPException(503, "Conexão com HCM não configurada")
        try:
            headers = {"Authorization": f"Bearer {token}"}
            if execution:
                execution_key = os.getenv("HCM_EXECUTION_TOKEN", "")
                if not execution_key:
                    raise HTTPException(503, "Credencial de execução em homologação não configurada")
                headers["X-HCM-Execution-Key"] = execution_key
            with httpx.Client(timeout=45, follow_redirects=False) as client:
                response = client.request("POST" if payload is not None else "GET", self.base + path,
                    headers=headers, params=params, json=payload)
            if response.status_code >= 400:
                if response.status_code in (403, 404, 409, 422):
                    detail = response.json().get("detail", "Operação recusada pelo HCM")
                    raise HTTPException(response.status_code, detail)
                raise HTTPException(502, f"HCM recusou a consulta (HTTP {response.status_code}); confira conexão e escopo")
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(502, "Não foi possível consultar o HCM; nenhuma conclusão foi produzida") from exc

    def modules(self):
        """Parametrizable modules published by the HCM, cached briefly; the ERP never hard-codes this list."""
        if _modules_cache["items"] is None or time.monotonic() - _modules_cache["at"] > 60:
            _modules_cache.update(at=time.monotonic(), items=self.call("/modules")["items"])
        return _modules_cache["items"]

    def snapshot(self, as_of, company_code):
        if not company_code:
            raise HTTPException(409, "Identifique a empresa no atendimento antes de consultar as configurações")
        return self.call(self.path + "/snapshot", params={"company_code": company_code, "as_of": str(as_of)})

    def catalog(self):
        return self.call("/catalog", params={"module": self.module})

    def companies(self, query=""):
        return self.call("/companies", params={"query": query})

    def validate(self, snapshot, payloads, assignments=None):
        """Vacations validates the whole plan; other modules validate their single company configuration."""
        if self.module == "vacations":
            return self.call(self.path + "/validate", payload={"company_code": snapshot["company"]["code"], "effective_from": snapshot["as_of"],
                                                                "configurations": payloads, "assignments": assignments})
        errors = []
        for payload in payloads:
            report = self.call(self.path + "/validate", payload=payload)
            errors.extend(report.get("errors", []))
        return {"valid": not errors, "errors": errors, "persisted": False}

    def apply(self, packet):
        if self.module != "vacations":
            packet = {key: value for key, value in packet.items() if key != "assignments"}
        return self.call(self.path + "/apply", payload=packet, execution=True)

    def operation(self, operation_id):
        return self.call(f"{self.path}/operations/{operation_id}")

    def verify(self, operation_id):
        return self.call(f"{self.path}/operations/{operation_id}/verify")

    def rollback(self, operation_id, expected_fingerprint, reason):
        return self.call(f"{self.path}/operations/{operation_id}/rollback", payload={"expected_fingerprint": expected_fingerprint, "reason": reason}, execution=True)

def get_hcm(request: Request, db=Depends(get_db)):
    from app.models import Case
    case_id = request.path_params.get("case_id")
    row = db.get(Case, int(case_id)) if case_id is not None else None
    module = (row.module or (row.workflow or {}).get("module") or "vacations") if row else "vacations"
    return HCMClient(module)
