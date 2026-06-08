"""Registry CRUD for ModelRegistry rows. The composite id is the swap point."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import ModelEvalRun, ModelRegistry


def model_id_for(target_id: str, implementation: str) -> str:
    return f"{target_id}__{implementation}"


def register_model(
    db: Session,
    *,
    target_id: str,
    implementation: str,
    model_name: Optional[str] = None,
    config: Optional[dict] = None,
) -> ModelRegistry:
    mid = model_id_for(target_id, implementation)
    existing = db.get(ModelRegistry, mid)
    if existing is not None:
        return existing
    row = ModelRegistry(
        id=mid,
        target_id=target_id,
        implementation=implementation,
        model_name=model_name,
        config=config,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def list_models(db: Session) -> list[ModelRegistry]:
    stmt = select(ModelRegistry).where(ModelRegistry.archived_at.is_(None)).order_by(ModelRegistry.created_at.desc())
    return list(db.scalars(stmt).all())


def get_model(db: Session, model_id: str) -> Optional[ModelRegistry]:
    return db.get(ModelRegistry, model_id)


def get_last_eval(db: Session, model_id: str) -> Optional[ModelEvalRun]:
    stmt = (
        select(ModelEvalRun)
        .where(ModelEvalRun.model_id == model_id)
        .order_by(ModelEvalRun.started_at.desc())
        .limit(1)
    )
    return db.scalar(stmt)


def recent_eval_runs(db: Session, *, model_id: Optional[str] = None, limit: int = 50) -> list[ModelEvalRun]:
    stmt = select(ModelEvalRun).order_by(ModelEvalRun.started_at.desc()).limit(limit)
    if model_id:
        stmt = (
            select(ModelEvalRun)
            .where(ModelEvalRun.model_id == model_id)
            .order_by(ModelEvalRun.started_at.desc())
            .limit(limit)
        )
    return list(db.scalars(stmt).all())
