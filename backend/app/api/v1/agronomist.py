"""Agronomist endpoints — the human review queue.

Access control: ``agronomist`` and ``admin`` roles only (RBAC enforced by
``Depends(require_roles(...))``; admins implicitly pass every check).

A diagnosis reaches the queue when Stage-2 confidence lands in
[``DISEASE_REVIEW_THRESHOLD``, ``DISEASE_CONFIRM_THRESHOLD``).  The agronomist
can confirm (optionally authoring a better treatment plan), reject, or ask the
farmer to retake the photo.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_roles
from app.core.database import utcnow
from app.models.diagnosis import Diagnosis, DiagnosisStatus
from app.models.user import UserRole
from app.schemas.diagnosis import (
    AgronomistStats,
    DiagnosisOut,
    ReviewDecisionRequest,
    ReviewQueueResponse,
    TreatmentPlan,
)
from app.services.ai_vision import build_treatment_plan

router = APIRouter(
    prefix="/agronomist",
    tags=["Agronomist"],
    dependencies=[Depends(require_roles(UserRole.AGRONOMIST, UserRole.ADMIN))],
)


def _queue_query() -> select:  # type: ignore[valid-type]
    return select(Diagnosis).where(
        Diagnosis.status == DiagnosisStatus.IN_REVIEW.value,
        Diagnosis.is_deleted.is_(False),
    )


@router.get("/review-queue", response_model=ReviewQueueResponse)
async def review_queue(
    db: DbSession,
    _: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    crop_id: str | None = Query(None, description="Filter by crop id"),
) -> ReviewQueueResponse:
    """Diagnoses waiting for an agronomist decision (oldest first)."""
    query = _queue_query()
    if crop_id:
        query = query.where(Diagnosis.crop_id == crop_id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                query.order_by(Diagnosis.updated_at.asc()).limit(limit).offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return ReviewQueueResponse(
        items=[DiagnosisOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/review-queue/{diagnosis_id}", response_model=DiagnosisOut)
async def review_item(diagnosis_id: str, db: DbSession, _: CurrentUser) -> DiagnosisOut:
    """Full detail of one queued diagnosis (incl. image quality + model meta)."""
    diagnosis = await db.get(Diagnosis, diagnosis_id)
    if diagnosis is None or diagnosis.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis not found")
    return DiagnosisOut.model_validate(diagnosis)


@router.post("/review-queue/{diagnosis_id}/decision", response_model=DiagnosisOut)
async def decide(
    diagnosis_id: str,
    payload: ReviewDecisionRequest,
    db: DbSession,
    agronomist: CurrentUser,
) -> DiagnosisOut:
    """Apply an agronomist decision to a queued diagnosis."""
    diagnosis = await db.get(Diagnosis, diagnosis_id)
    if diagnosis is None or diagnosis.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis not found")

    if diagnosis.status != DiagnosisStatus.IN_REVIEW.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Diagnosis is '{diagnosis.status}', only '{DiagnosisStatus.IN_REVIEW.value}' "
                "items can be decided."
            ),
        )

    if payload.action == "confirm":
        diagnosis.status = DiagnosisStatus.CONFIRMED.value
        if payload.treatment_plan is not None:
            diagnosis.treatment_plan = payload.treatment_plan.model_dump(mode="json")
        elif not diagnosis.treatment_plan and diagnosis.disease_label:
            # Agronomist confirmed without a custom plan -> generate the
            # standard bilingual plan for the (now confirmed) disease.
            diagnosis.treatment_plan = build_treatment_plan(
                diagnosis.disease_label, diagnosis.crop_label
            )
    elif payload.action == "reject":
        diagnosis.status = DiagnosisStatus.REJECTED.value
        diagnosis.treatment_plan = None
    else:  # retake
        diagnosis.status = DiagnosisStatus.RETAKE_REQUIRED.value
        diagnosis.treatment_plan = None

    diagnosis.review_notes = payload.notes
    diagnosis.reviewed_by = agronomist.id
    diagnosis.reviewed_at = utcnow()
    diagnosis.client_updated_at = utcnow()

    db.add(diagnosis)
    await db.commit()
    await db.refresh(diagnosis)

    # Keep the TreatmentPlan schema honest when we just wrote a generated plan.
    return DiagnosisOut.model_validate(diagnosis)


@router.get("/stats", response_model=AgronomistStats)
async def stats(db: DbSession, _: CurrentUser) -> AgronomistStats:
    """Counts per diagnosis status across the whole platform."""
    rows = (
        await db.execute(
            select(Diagnosis.status, func.count())
            .where(Diagnosis.is_deleted.is_(False))
            .group_by(Diagnosis.status)
        )
    ).all()
    payload: dict[str, int] = {status_.value: 0 for status_ in DiagnosisStatus}
    for status_value, count in rows:
        payload[status_value] = int(count)
    return AgronomistStats(
        queued=payload.get(DiagnosisStatus.QUEUED.value, 0),
        in_review=payload.get(DiagnosisStatus.IN_REVIEW.value, 0),
        confirmed=payload.get(DiagnosisStatus.CONFIRMED.value, 0),
        retake_required=payload.get(DiagnosisStatus.RETAKE_REQUIRED.value, 0),
        rejected=payload.get(DiagnosisStatus.REJECTED.value, 0),
        awaiting_crop_selection=payload.get(
            DiagnosisStatus.AWAITING_CROP_SELECTION.value, 0
        ),
        total=sum(payload.values()),
    )


@router.get("/treatment-template/{disease_id}", response_model=TreatmentPlan)
async def treatment_template(
    disease_id: str,
    _: CurrentUser,
    crop_id: str | None = Query(None, description="Optional crop id for context"),
) -> TreatmentPlan:
    """Standard bilingual treatment plan for a disease (agronomist reference)."""
    return TreatmentPlan.model_validate(build_treatment_plan(disease_id, crop_id))
