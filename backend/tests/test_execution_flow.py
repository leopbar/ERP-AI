import copy
import pytest
from fastapi import HTTPException
from test_workflow import setup, opened, manual, action, CATALOG, scope_key_of
from app.workflow import fingerprint
from app.workflow import checked_payloads
from app.contracts import Proposal
from test_workflow import PROPOSAL
from app.knowledge import retrieve

class Executor:
    """Fake of the HCM v2 apply/verify/rollback cycle over FakeHCM.data; snapshots are derived like the real HCM does."""
    def __init__(self, fake):
        self.fake = fake; self.operations = {}; self.calls = 0; self.lose_response = False; self.states = {}
    def current(self, packet_or_op):
        return self.fake.snapshot(packet_or_op["effective_from"], self.fake.data["company"]["code"])
    def apply(self, packet):
        self.calls += 1
        if packet["operation_id"] in self.operations:
            return self.operations[packet["operation_id"]]
        before = self.current(packet)
        if before["fingerprint"] != packet["expected_fingerprint"]:
            raise HTTPException(409, "HCM mudou")
        self.states[packet["operation_id"]] = copy.deepcopy(self.fake.data)
        created = []
        for item in packet["configurations"]:
            scope = scope_key_of(item["company_id"], item)
            rows = self.fake.data["schedules"]["homologation"]
            version = max((row["version"] for row in rows if row["scope_key"] == scope), default=0) + 1
            row = {"id": len(rows) + 1, "scope_key": scope, "environment": "homologation", "version": version, "active": True, **item}
            rows.append(row); created.append({"kind": "vacation", "id": row["id"]})
        if packet.get("assignments"):
            previous = self.fake.data["assignments"]["homologation"]
            self.fake.data["assignments"]["homologation"] = {**packet["assignments"], "version": previous["version"] + 1, "effective_from": packet["effective_from"]}
            created.append({"kind": "module", "id": previous["version"] + 1})
        after = self.current(packet)
        operation = {"operation_id": packet["operation_id"], "status": "applied", "approved_by": packet["approved_by"], "effective_from": packet["effective_from"],
                     "before_snapshot": before, "after_snapshot": after, "created_config_ids": created, "approval_hash": packet["approval_hash"],
                     "report": {"passed": True, "checks": [{"name": "Teste oficial", "passed": True, "origin": "HCM"}]}}
        self.operations[packet["operation_id"]] = operation
        if self.lose_response:
            self.lose_response = False
            raise HTTPException(502, "Resposta perdida após commit")
        return operation
    def verify(self, operation_id):
        op = self.operations[operation_id]
        current = self.current(op)
        return {"status": op["status"], "current_snapshot": current, "report": {"passed": current["fingerprint"] == op["after_snapshot"]["fingerprint"], "checks": []}}
    def rollback(self, operation_id, expected, reason):
        op = self.operations[operation_id]
        if self.current(op)["fingerprint"] != expected:
            raise HTTPException(409, "Mudança posterior")
        self.fake.data = copy.deepcopy(self.states[operation_id])
        op["status"] = "reverted"; op["report"]["rollback"] = {"passed": True, "reason": reason}
        return op

def connected(setup):
    client, fake, factory = setup
    executor = Executor(fake)
    fake.apply = executor.apply; fake.verify = executor.verify; fake.rollback = executor.rollback
    row = opened(client); response = manual(client, row)
    assert response.status_code == 200
    return client, fake, factory, executor, response.json()

def test_apply_confirm_and_rollback_without_manual_evidence(setup):
    client, fake, factory, executor, row = connected(setup)
    response = action(client, row, "execute", approval_hash=row["approval_hash"])
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["status"] == "applied" and row["result"]["automatic_tests"]["passed"]
    assert row["result"]["configuration_matches"] and row["result"]["production_unchanged"]
    assert action(client, row, "analyze").status_code == 409
    row = action(client, row, "confirm-result").json()
    assert row["status"] == "validated"
    with factory() as db:
        assert len(retrieve(db, "parcelas", CATALOG, company_code="EMP001")) == 2
    row = action(client, row, "rollback").json()
    assert row["status"] == "reverted"
    with factory() as db:
        assert len(retrieve(db, "parcelas", CATALOG, company_code="EMP001")) == 1

def test_lost_response_resume_is_idempotent(setup):
    client, fake, _, executor, row = connected(setup)
    executor.lose_response = True
    assert action(client, row, "execute", approval_hash=row["approval_hash"]).status_code == 502
    row = client.get(f"/api/cases/{row['id']}").json()
    assert row["status"] == "execution_unknown"
    operation_id = row["result"]["execution_request"]["operation_id"]
    assert action(client, row, "review", decision="reject", comment="Não descartar execução pendente").status_code == 409
    response = action(client, row, "execute", approval_hash=row["approval_hash"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "applied"
    assert response.json()["result"]["operation_id"] == operation_id
    assert len(fake.data["schedules"]["homologation"]) == 2

def test_tampered_approval_and_stale_context_are_blocked(setup):
    client, fake, _, executor, row = connected(setup)
    assert action(client, row, "execute", approval_hash="0"*64).status_code == 409
    assert executor.calls == 0
    fake.data["schedules"]["production"][0]["version"] += 1
    assert action(client, row, "execute", approval_hash=row["approval_hash"]).status_code == 409
    assert executor.calls == 0

def test_change_after_execution_blocks_confirmation_and_reversal(setup):
    client, fake, _, executor, row = connected(setup)
    row = action(client, row, "execute", approval_hash=row["approval_hash"]).json()
    fake.data["schedules"]["homologation"][-1]["min_advance_days"] += 1
    assert action(client, row, "confirm-result").status_code == 409
    assert action(client, row, "rollback").status_code == 409

def test_new_group_rule_uses_planned_parent_and_can_preserve_exception(setup):
    _, fake, _ = setup
    proposal = copy.deepcopy(PROPOSAL)
    proposal["changes"].append({"group_id": 10, "parameter": "min_advance_days", "value": 45, "reason": "Grupo exige antecedência diferente"})
    snapshot = fake.snapshot("2026-10-31", "EMP001")
    payloads, assignments = checked_payloads(Proposal.model_validate(proposal), snapshot, CATALOG, fake)
    assert assignments is None and payloads[0]["max_installments"] == 3
    assert payloads[1]["max_installments"] == 3 and payloads[1]["min_advance_days"] == 45
    proposal["changes"].append({"group_id": 10, "parameter": "max_installments", "value": 2, "reason": "Exceção do grupo ao novo limite geral"})
    payloads, _ = checked_payloads(Proposal.model_validate(proposal), snapshot, CATALOG, fake)
    assert payloads[1]["max_installments"] == 2

def test_group_membership_change_invalidates_approval(setup):
    client, fake, _, executor, row = connected(setup)
    fake.data["groups"][0]["employee_count"] += 1
    assert action(client, row, "execute", approval_hash=row["approval_hash"]).status_code == 409
    assert executor.calls == 0

def test_rollback_pending_is_removed_from_memory_before_remote_call(setup):
    client, fake, factory, executor, row = connected(setup)
    row = action(client, row, "execute", approval_hash=row["approval_hash"]).json()
    row = action(client, row, "confirm-result").json()
    real_rollback = fake.rollback
    def lost_response(operation_id, expected, reason):
        with factory() as db:
            assert len(retrieve(db, "parcelas", CATALOG, company_code="EMP001")) == 1
        result = real_rollback(operation_id, expected, reason)
        fake.rollback = lambda *_args: result
        raise HTTPException(502, "Resposta perdida após reversão")
    fake.rollback = lost_response
    assert action(client, row, "rollback").status_code == 502
    row = client.get(f"/api/cases/{row['id']}").json()
    assert row["status"] == "rollback_unknown"
    response = action(client, row, "rollback")
    assert response.status_code == 200 and response.json()["status"] == "reverted"

def test_invalid_model_proposal_is_audited_and_used_as_feedback(setup, monkeypatch):
    import app.main as main_module
    client, fake, _, _, row = connected(setup)
    invalid = copy.deepcopy(PROPOSAL)
    invalid["changes"][0]["parameter"] = "nonexistent"
    monkeypatch.setattr(main_module, "generate", lambda *_: (Proposal.model_validate(invalid), {"mode": "model"}))
    assert action(client, row, "analyze").status_code == 422
    row = client.get(f"/api/cases/{row['id']}").json()
    assert row["status"] == "proposal_failed" and not row["proposal"]
    assert row["events"][-1]["type"] == "proposal_failed"
    def repaired(request, snapshot, sources, feedback):
        assert any("tentativa anterior" in item for item in feedback)
        return Proposal.model_validate(PROPOSAL), {"mode": "model"}
    monkeypatch.setattr(main_module, "generate", repaired)
    response = action(client, row, "analyze")
    assert response.status_code == 200 and response.json()["status"] == "review"
