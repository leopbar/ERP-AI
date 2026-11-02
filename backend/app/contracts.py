from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class NewCase(StrictModel):
    request: str = Field(min_length=10, max_length=8000)
    effective_from: date
    company_code: str = Field(default="", max_length=20)

class Change(StrictModel):
    """One parameter value in one scope. Scope: config_code (named configuration) > group_id (group rule) > company rule.
    The value follows the JSON Schema the HCM catalog publishes for the parameter."""
    group_id: int | None = None
    config_code: str | None = Field(default=None, max_length=20)
    parameter: str
    value: Any
    reason: str = Field(min_length=3, max_length=2000)

    def scope(self):
        if self.config_code:
            return "config", self.config_code
        if self.group_id is not None:
            return "group", self.group_id
        return "company", None

class NamedConfiguration(StrictModel):
    config_code: str = Field(pattern=r"^[A-Z0-9_-]{1,20}$")
    name: str = Field(min_length=1, max_length=120)
    simulation_text: str | None = Field(default=None, max_length=2000)

class Assignment(StrictModel):
    group: str = Field(min_length=1, max_length=120)
    config: str = Field(min_length=1, max_length=20)

class Assignments(StrictModel):
    vacations: list[Assignment] = Field(default_factory=list, max_length=100)
    vacations_simulation: list[Assignment] = Field(default_factory=list, max_length=100)

class Proposal(StrictModel):
    summary: str = Field(min_length=3, max_length=4000)
    questions: list[str]
    changes: list[Change]
    tests: list[str]
    sources: list[str]
    named_configurations: list[NamedConfiguration] = Field(default_factory=list, max_length=20)
    assignments: Assignments | None = None

class Action(StrictModel):
    revision: int = Field(ge=1)
    comment: str = Field(default="", max_length=8000)

class ApplyAction(Action):
    approval_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

class ManualProposal(Action):
    proposal: Proposal

class Review(Action):
    decision: Literal["accept", "correct", "reject"]

class Evidence(StrictModel):
    test: str = Field(min_length=3, max_length=2000)
    passed: bool
    evidence: str = Field(min_length=5, max_length=4000)

class Finish(Action):
    evidence: list[Evidence]
