from datetime import datetime, timezone
from sqlalchemy import Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now():
    return datetime.now(timezone.utc).isoformat()

class Base(DeclarativeBase):
    pass

class Case(Base):
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    request: Mapped[str] = mapped_column(Text)
    effective_from: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(40), default="open")
    company_code: Mapped[str] = mapped_column(String(20), default="")
    module: Mapped[str] = mapped_column(String(40), default="")
    catalog_version: Mapped[str] = mapped_column(String(80), default="")
    contract_hash: Mapped[str] = mapped_column(String(64), default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    proposal: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow: Mapped[dict] = mapped_column(JSON, default=dict)
    events: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
    updated_at: Mapped[str] = mapped_column(String(40), default=now)
    __mapper_args__ = {"version_id_col": revision}


class InterpretationMemory(Base):
    __tablename__ = "interpretation_memories"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(Integer, index=True)
    actor: Mapped[str] = mapped_column(String(100), index=True)
    company_code: Mapped[str] = mapped_column(String(20))
    module: Mapped[str] = mapped_column(String(40))
    catalog_version: Mapped[str] = mapped_column(String(80))
    contract_hash: Mapped[str] = mapped_column(String(64), default="")
    request: Mapped[str] = mapped_column(Text)
    meaning: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[str] = mapped_column(String(40), default=now)
