from typing import Any, Dict, List
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_role
from app.models.user import UserRole
from app.services.data_transfer import data_transfer_service

router = APIRouter(prefix="/admin/data", tags=["Data Import/Export & Bulk Operations"])


class ImportCSVRequest(BaseModel):
    csv_content: str


@router.get(
    "/export/roadmap/json",
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def export_roadmap_json(db: Session = Depends(get_db)):
    data = data_transfer_service.export_roadmap_json(db)
    return {"roadmap": data}


@router.get(
    "/export/roadmap/csv",
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def export_roadmap_csv(db: Session = Depends(get_db)):
    csv_str = data_transfer_service.export_roadmap_csv(db)
    return Response(
        content=csv_str,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=roadmap.csv"},
    )


@router.post(
    "/import/roadmap/json",
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def import_roadmap_json(
    payload: List[Dict[str, Any]], db: Session = Depends(get_db)
):
    try:
        count = data_transfer_service.import_roadmap_json(db, payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"message": "Roadmap imported successfully", "imported_topics": count}


@router.get(
    "/export/resources/csv",
    dependencies=[
        Depends(
            require_role(
                [UserRole.TECH_ADMIN, UserRole.CONTENT_ADMIN, UserRole.VIEWER]
            )
        )
    ],
)
def export_resources_csv(db: Session = Depends(get_db)):
    csv_str = data_transfer_service.export_resources_csv(db)
    return Response(
        content=csv_str,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=resources.csv"},
    )


@router.post(
    "/import/resources/csv",
    dependencies=[Depends(require_role([UserRole.TECH_ADMIN]))],
)
def import_resources_csv(
    payload: ImportCSVRequest, db: Session = Depends(get_db)
):
    try:
        count = data_transfer_service.import_resources_csv(db, payload.csv_content)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return {"message": "Resources imported successfully", "imported_count": count}
