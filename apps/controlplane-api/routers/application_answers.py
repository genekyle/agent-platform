"""Application-answers routes — the operator's repeatable Indeed application answers.

Extracted from main.py (router split — docs/PLAN_main-split.md). Shared helpers come
from deps (get_db, _slugify); _answer_dict is local to this domain.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from deps import _slugify, get_db
from models import ApplicationAnswer

router = APIRouter()


class ApplicationAnswerWrite(BaseModel):
    answer_key: Optional[str] = None  # derived from display_name if omitted
    display_name: str
    category: str = "custom"
    value: str = ""
    question_patterns: list[str] = []
    input_hint: str = "text"
    options: list[str] = []
    notes: Optional[str] = None


class ApplicationAnswerUpdate(BaseModel):
    display_name: Optional[str] = None
    category: Optional[str] = None
    value: Optional[str] = None
    question_patterns: Optional[list[str]] = None
    input_hint: Optional[str] = None
    options: Optional[list[str]] = None
    notes: Optional[str] = None
    status: Optional[str] = None


class QuestionMatchRequest(BaseModel):
    question: str


def _answer_dict(a: ApplicationAnswer) -> dict[str, Any]:
    return {
        "answer_key": a.answer_key, "display_name": a.display_name, "category": a.category,
        "value": a.value, "question_patterns": a.question_patterns or [],
        "input_hint": a.input_hint, "options": a.options or [], "notes": a.notes,
        "source": a.source, "status": a.status,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


@router.get("/api/application-answers")
def list_application_answers(category: Optional[str] = None, db: Session = Depends(get_db)):
    """The operator's repeatable application answers (demographics, salary, ...),
    grouped by category for the Indeed workspace editor."""
    stmt = select(ApplicationAnswer).where(ApplicationAnswer.status == "active")
    if category:
        stmt = stmt.where(ApplicationAnswer.category == category)
    rows = db.scalars(stmt.order_by(ApplicationAnswer.category, ApplicationAnswer.display_name)).all()
    by_cat: dict[str, list[dict]] = {}
    for a in rows:
        by_cat.setdefault(a.category, []).append(_answer_dict(a))
    return {"total": len(rows), "by_category": by_cat,
            "answers": [_answer_dict(a) for a in rows]}


@router.post("/api/application-answers")
def upsert_application_answer(body: ApplicationAnswerWrite, db: Session = Depends(get_db)):
    name = (body.display_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="display_name is required")
    key = (body.answer_key or _slugify(name)).strip()
    if not key:
        raise HTTPException(status_code=400, detail="Could not derive an answer key")
    row = db.get(ApplicationAnswer, key)
    if row is None:
        row = ApplicationAnswer(answer_key=key, source="human")
        db.add(row)
    row.display_name = name
    row.category = (body.category or "custom").strip() or "custom"
    row.value = body.value or ""
    row.question_patterns = body.question_patterns or []
    row.input_hint = body.input_hint or "text"
    row.options = body.options or []
    row.notes = body.notes
    row.status = "active"
    db.commit()
    db.refresh(row)
    return _answer_dict(row)


@router.patch("/api/application-answers/{answer_key}")
def update_application_answer(answer_key: str, body: ApplicationAnswerUpdate,
                             db: Session = Depends(get_db)):
    row = db.get(ApplicationAnswer, answer_key)
    if row is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    for field in ("display_name", "category", "value", "question_patterns",
                  "input_hint", "options", "notes", "status"):
        val = getattr(body, field)
        if val is not None:
            setattr(row, field, val)
    db.commit()
    db.refresh(row)
    return _answer_dict(row)


@router.delete("/api/application-answers/{answer_key}")
def delete_application_answer(answer_key: str, db: Session = Depends(get_db)):
    row = db.get(ApplicationAnswer, answer_key)
    if row is None:
        raise HTTPException(status_code=404, detail="Answer not found")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": answer_key}


@router.post("/api/application-answers/match")
def match_application_answer(body: QuestionMatchRequest, db: Session = Depends(get_db)):
    """Map an on-screen application question to a stored answer via cheap keyword
    overlap. `matched=False` means the caller should escalate to Haiku (the future
    form-fill executor's cheapest-first cascade). No model, no API cost here."""
    import application_answers as aa
    rows = db.scalars(select(ApplicationAnswer).where(ApplicationAnswer.status == "active")).all()
    answers = [_answer_dict(a) for a in rows]
    return aa.match_question(body.question, answers)
