"""Best-effort progress. Separate short transactions, pool and a strict data allowlist.

No prompts, responses, credentials or business payloads are copied here. Observation
failures are swallowed, and never commit/rollback the business session. State in
cases/checkpoints remains authoritative. Works across backend processes.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
import math
import re
import time
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import WorkflowProgress, now

_current = ContextVar("workflow_observer", default=None)
_stage = ContextVar("workflow_stage", default=None)
_model_context = ContextVar("workflow_model_context", default={})
NODE_STEPS = {"guard_input": "scope", "classify": "classification", "preview": "company",
              "context": "context", "context_expert": "context", "alternatives": "interpretations",
              "remember": "intent", "choice": "choice", "guide": "company", "clarify": "company",
              "consultant": "consultant"}
PAUSES = {"choice", "guide", "clarify", "consultant"}
# The stage a conditional edge is recorded under: the stage of the node it leaves.
ROUTE_STEPS = {**NODE_STEPS, "plan": "validation"}
GRAPH_NODES = set(ROUTE_STEPS) | {"__end__"}
STEPS = set(NODE_STEPS.values()) | {"configuration", "validation", "approval", "apply", "tests", "experience", "rollback",
                                      "catalog", "snapshot", "knowledge"}
ACTIVITIES = {"context_prepare", "model_prepare", "model_request", "model_response", "model_decode", "output_validate",
              "output_retry", "alternatives_organize", "routing", "sources_validate", "coverage_validate", "official_validation",
              "company_lookup", "catalog_read", "snapshot_read", "knowledge_retrieve", "scope_check", "modules_read",
              "intent_confirm", "context_check", "hcm_apply", "hcm_verify", "hcm_rollback", "result_compare", "reference_read",
              "route"}
OPERATIONS = {"analysis", "execute", "verify", "confirm", "rollback"}
HISTORY_LIMIT = 20


def safe_metadata(values):
    """An explicit primitive allowlist; never serialize arbitrary provider/config data."""
    result = {}
    for key, value in values.items():
        if key in {"model", "returned_model", "module"} and isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}", value) and not value.startswith("sk-"):
            result[key] = value
        elif key == "reasoning_effort" and value in ("none", "minimal", "low", "medium", "high", "xhigh", "not_supported"):
            result[key] = value
        elif key == "finish_reason" and value in ("stop", "length", "content_filter", "tool_calls"):
            result[key] = value
        elif key in {"attempt", "request_attempt", "max_completion_tokens", "prompt_tokens", "completion_tokens", "total_tokens", "reasoning_tokens", "cached_tokens", "source_count", "memory_count", "option_count", "http_status"} and type(value) is int and 0 <= value <= 10**12:
            result[key] = value
        elif key in {"duration_seconds", "estimated_usd"} and type(value) in (int, float) and math.isfinite(value) and 0 <= value < 10**12:
            result[key] = value
        elif key in {"repair", "escalated"} and type(value) is bool:
            result[key] = value
        elif key in {"source", "target"} and value in GRAPH_NODES:
            result[key] = value
    return result


def usage_metadata(usage):
    if not isinstance(usage, dict):
        return {}
    values = {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
    for name, key in (("completion_tokens_details", "reasoning_tokens"), ("prompt_tokens_details", "cached_tokens")):
        details = usage.get(name)
        if isinstance(details, dict):
            values[key] = details.get(key)
    return safe_metadata(values)


@lru_cache(maxsize=8)
def storage_engine(bind):
    if bind.dialect.name == "sqlite" and bind.url.database in (None, "", ":memory:"):
        # In-memory SQLite cannot provide independent transactions/connections.
        return None
    options = {"pool_size": 2, "max_overflow": 0, "pool_timeout": .1}
    options["connect_args"] = {"connect_timeout": 1} if bind.dialect.name == "postgresql" else {"timeout": .1}
    return create_engine(bind.url, **options)


class Recorder:
    def __init__(self, bind, case_id, run_id=None, base_revision=None, operation="analysis", flow_id=None):
        self.bind, self.case_id, self.run_id = bind, case_id, run_id or str(uuid4())
        self.data = {"events": [], "active": True, "updated_at": now(), "started_at": now(), "base_revision": base_revision,
                     "operation": operation if operation in OPERATIONS else "analysis", "flow_id": flow_id, "history": [], "history_truncated": False, "events_truncated": False}
        self.disabled = False
        self.loaded = False

    def emit(self, step, status="running", activity=None, span_id=None, **metadata):
        if self.disabled or step not in STEPS or status not in ("running", "done", "waiting", "error"):
            return
        self.data = {**self.data, "step": step, "state": status, "updated_at": now()}
        entry = {"step": step, "state": status, "at": self.data["updated_at"]}
        if activity in ACTIVITIES:
            entry.update(activity=activity, meta=safe_metadata(metadata))
            if span_id:
                entry["span_id"] = span_id
        entries = [*self.data["events"], entry]
        self.data["events_truncated"] = self.data["events_truncated"] or len(entries) > 160
        self.data["events"] = entries[-160:]
        self.persist()

    def persist(self):
        if self.disabled:
            return
        try:
            engine = storage_engine(self.bind)
            if engine is None:
                self.disabled = True
                return
            with engine.begin() as conn:
                if engine.dialect.name == "postgresql":
                    conn.exec_driver_sql("SET LOCAL statement_timeout = '250ms'")
                    conn.exec_driver_sql("SET LOCAL lock_timeout = '100ms'")
                with Session(bind=conn) as session:
                    row = session.get(WorkflowProgress, self.case_id)
                    if not self.loaded:
                        old = row.data if row else {}
                        history = list(old.get("history", []))
                        if row and old.get("events"):
                            history.append({"run_id": row.run_id, **{key: old.get(key) for key in
                                ("events", "events_truncated", "active", "started_at", "updated_at", "base_revision", "operation", "flow_id", "step", "state")}})
                        self.data["history"] = history[-HISTORY_LIMIT:]
                        self.data["history_truncated"] = bool(old.get("history_truncated") or len(history) > HISTORY_LIMIT)
                        self.loaded = True
                    if row is None:
                        session.add(WorkflowProgress(case_id=self.case_id, run_id=self.run_id, data=self.data))
                    else:
                        row.run_id, row.data = self.run_id, self.data
                    session.flush()
        except Exception:
            # Circuit breaker: at most one observation failure per invocation.
            self.disabled = True

    def finish(self, failed=False):
        if not self.data.get("step"):
            return
        self.data = {**self.data, "active": False, "updated_at": now()}
        if failed and self.data.get("step"):
            self.emit(self.data["step"], "error")
        else:
            self.persist()


def emit(step, status="running", activity=None, span_id=None, **metadata):
    recorder = _current.get()
    if recorder is not None:
        recorder.emit(step, status, activity=activity, span_id=span_id, **metadata)


def current_step():
    return _stage.get()


def model_metadata():
    return _model_context.get()


@contextmanager
def model_context(**metadata):
    token = _model_context.set(safe_metadata(metadata))
    try:
        yield
    finally:
        _model_context.reset(token)


@contextmanager
def span(step, activity, **metadata):
    values = dict(metadata)
    span_id, started = str(uuid4()), time.monotonic()
    emit(step, activity=activity, span_id=span_id, **values)
    try:
        yield values
    except Exception:
        emit(step, "error", activity=activity, span_id=span_id, **{**values, "duration_seconds": round(time.monotonic() - started, 3)})
        raise
    else:
        emit(step, "done", activity=activity, span_id=span_id, **{**values, "duration_seconds": round(time.monotonic() - started, 3)})


@contextmanager
def observe(bind, case_id, base_revision=None, operation="analysis", flow_id=None):
    recorder = Recorder(bind, case_id, base_revision=base_revision, operation=operation, flow_id=flow_id)
    token = _current.set(recorder)
    try:
        yield recorder
    except Exception:
        recorder.finish(failed=True)
        raise
    else:
        recorder.finish()
    finally:
        _current.reset(token)


def node(name, fn):
    @wraps(fn)
    def observed(state):
        step = NODE_STEPS.get(name)
        if step:
            emit(step, "waiting" if name in PAUSES else "running")
        token = _stage.set(step or ("configuration" if name == "plan" else None))
        try:
            result = fn(state)
        finally:
            _stage.reset(token)
        if step:
            emit(step, "done")
        return result
    return observed


def route(source, fn):
    """Records which way a conditional edge went: node names only, never state values."""
    @wraps(fn)
    def observed(state):
        target = fn(state)
        emit(ROUTE_STEPS[source], "done", activity="route", source=source, target=target)
        return target
    return observed


def read(bind, case_id):
    try:
        engine = storage_engine(bind)
        if engine is None:
            return None
        with engine.connect() as conn:
            if engine.dialect.name == "postgresql":
                conn.exec_driver_sql("SET LOCAL statement_timeout = '250ms'")
            with Session(bind=conn) as session:
                row = session.get(WorkflowProgress, case_id)
                return {"run_id": row.run_id, **row.data} if row else None
    except Exception:
        return None


def tracked(fn):
    @wraps(fn)
    def observed(*args, **kwargs):
        with observe(kwargs["db"].get_bind(), kwargs["case_id"], kwargs["payload"].revision, operation=fn.__name__):
            return fn(*args, **kwargs)
    return observed
