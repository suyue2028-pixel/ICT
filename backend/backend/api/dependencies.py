from typing import Annotated

from fastapi import Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.db.session import get_db
from backend.schemas.contracts import Page

Database = Annotated[Session, Depends(get_db)]
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def require_record(db: Session, model, identifier):
    record = db.get(model, identifier)
    if record is None:
        raise HTTPException(404, "record_not_found")
    return record


def paginate(db, query, schema, page, page_size):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    items = db.scalars(query.offset((page - 1) * page_size).limit(page_size)).all()
    return Page[schema](items=items, total=total, page=page, page_size=page_size)
