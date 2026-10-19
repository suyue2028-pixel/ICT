from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend.api.dependencies import Database
from backend.core.security import require_admin
from backend.models import DataSource
from backend.schemas.contracts import TaskRead
from backend.services.jobs import create_task

router = APIRouter(prefix="/demo", tags=["离线开发验证"])


@router.post("/import", response_model=TaskRead, status_code=202, dependencies=[Depends(require_admin)])
def import_demo(db: Database):
    source = db.scalars(select(DataSource).where(DataSource.adapter == "offline")
                        .order_by(DataSource.created_at)).first()
    task = create_task(db, source)
    db.commit()
    return task
