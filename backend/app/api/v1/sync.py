"""Offline-first sync engine (last-write-wins).

Contract
--------
* ``POST /sync/push`` — devices upload farms/plots/diagnoses created while
  offline.  Each record carries a device-generated ``client_id`` (idempotency
  key) and ``updated_at`` (device clock).  **Last write wins**: the newest
  ``updated_at`` becomes canonical; when the server keeps its copy, the result
  is ``server_wins`` and includes the server record so the client can reconcile.
* ``GET /sync/pull``  — incremental download of everything newer than the
  client's cursor (``since`` + ``since_id`` keyset pagination).

Ordering guarantees
-------------------
Farms are applied before plots, and plots before diagnoses, so a device can
push a whole offline batch in one request.  A plot whose farm is unknown in
the same batch is rejected with ``parent_farm_not_found`` (re-push after the
farm lands).

Data integrity guards
---------------------
* Everything is scoped to the authenticated user (no cross-account writes).
* A diagnosis already processed on the server (``in_review``/``confirmed``/
  ``rejected``) can never be downgraded back to ``queued`` by an offline client.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbSession
from app.core.database import ensure_utc, new_id, utcnow
from app.models.diagnosis import Diagnosis, DiagnosisStatus
from app.models.farm import Farm, Plot
from app.schemas.sync import (
    SYNC_BATCH_LIMIT,
    SyncAction,
    SyncDiagnosisOut,
    SyncDiagnosisRecord,
    SyncFarmOut,
    SyncFarmRecord,
    SyncPlotOut,
    SyncPlotRecord,
    SyncPushRequest,
    SyncPushResponse,
    SyncPullResponse,
    SyncRecordResult,
)

logger = logging.getLogger("hassad.api.sync")

router = APIRouter(prefix="/sync", tags=["Sync"])


# ======================================================================
# Last-write-wins helpers
# ======================================================================
def _incoming_time(value: datetime | None) -> datetime | None:
    return ensure_utc(value)


def _stored_time(updated_at: datetime | None, client_updated_at: datetime | None) -> datetime:
    return ensure_utc(client_updated_at or updated_at) or utcnow()


def _wins(incoming: datetime | None, stored: datetime) -> bool:
    """Newest timestamp wins; ties go to the server (deterministic)."""
    return incoming is not None and incoming > stored


async def _sync_farm(
    db: AsyncSession, owner_id: str, record: SyncFarmRecord
) -> tuple[SyncRecordResult, str]:
    existing = (
        await db.execute(
            select(Farm).where(Farm.owner_id == owner_id, Farm.client_id == record.client_id)
        )
    ).scalar_one_or_none()

    incoming = _incoming_time(record.updated_at)
    now = utcnow()

    if existing is None:
        farm = Farm(
            id=new_id(),
            client_id=record.client_id,
            owner_id=owner_id,
            name=record.name,
            location_text=record.location_text,
            latitude=record.latitude,
            longitude=record.longitude,
            area_hectares=record.area_hectares,
            main_crop=record.main_crop,
            notes=record.notes,
            is_deleted=record.is_deleted,
            client_updated_at=incoming,
        )
        db.add(farm)
        await db.flush()
        logger.info("Sync: created farm %s (client_id=%s)", farm.id, record.client_id)
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=farm.id,
                action=SyncAction.CREATED,
                server_updated_at=now,
            ),
            farm.id,
        )

    stored = _stored_time(existing.updated_at, existing.client_updated_at)
    if _wins(incoming, stored):
        existing.name = record.name
        existing.location_text = record.location_text
        existing.latitude = record.latitude
        existing.longitude = record.longitude
        existing.area_hectares = record.area_hectares
        existing.main_crop = record.main_crop
        existing.notes = record.notes
        existing.is_deleted = record.is_deleted
        existing.client_updated_at = incoming
        db.add(existing)
        await db.flush()
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=existing.id,
                action=SyncAction.UPDATED,
                server_updated_at=utcnow(),
            ),
            existing.id,
        )

    return (
        SyncRecordResult(
            client_id=record.client_id,
            server_id=existing.id,
            action=SyncAction.SERVER_WINS,
            server_updated_at=ensure_utc(existing.updated_at) or now,
            reason="Server copy is newer than the device copy (last-write-wins).",
            server_record=SyncFarmOut.model_validate(existing).model_dump(mode="json"),
        ),
        existing.id,
    )


async def _resolve_farm_id(
    db: AsyncSession, owner_id: str, farm_client_id: str, farm_map: dict[str, str]
) -> str | None:
    if farm_client_id in farm_map:
        return farm_map[farm_client_id]
    return (
        await db.execute(
            select(Farm.id).where(Farm.owner_id == owner_id, Farm.client_id == farm_client_id)
        )
    ).scalar_one_or_none()


async def _sync_plot(
    db: AsyncSession, owner_id: str, record: SyncPlotRecord, farm_map: dict[str, str]
) -> tuple[SyncRecordResult, str | None]:
    farm_id = await _resolve_farm_id(db, owner_id, record.farm_client_id, farm_map)
    if farm_id is None:
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=None,
                action=SyncAction.REJECTED,
                server_updated_at=utcnow(),
                reason=(
                    f"parent_farm_not_found: no farm with client_id '{record.farm_client_id}'. "
                    "Push the farm first, then re-push this plot."
                ),
            ),
            None,
        )

    existing = (
        await db.execute(
            select(Plot).where(Plot.farm_id == farm_id, Plot.client_id == record.client_id)
        )
    ).scalar_one_or_none()

    incoming = _incoming_time(record.updated_at)
    now = utcnow()

    if existing is None:
        plot = Plot(
            id=new_id(),
            client_id=record.client_id,
            farm_id=farm_id,
            name=record.name,
            area_hectares=record.area_hectares,
            current_crop=record.current_crop,
            soil_type=record.soil_type,
            irrigation_method=record.irrigation_method,
            planted_at=record.planted_at,
            expected_harvest_at=record.expected_harvest_at,
            notes=record.notes,
            is_deleted=record.is_deleted,
            client_updated_at=incoming,
        )
        db.add(plot)
        await db.flush()
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=plot.id,
                action=SyncAction.CREATED,
                server_updated_at=now,
            ),
            plot.id,
        )

    stored = _stored_time(existing.updated_at, existing.client_updated_at)
    if _wins(incoming, stored):
        existing.name = record.name
        existing.area_hectares = record.area_hectares
        existing.current_crop = record.current_crop
        existing.soil_type = record.soil_type
        existing.irrigation_method = record.irrigation_method
        existing.planted_at = record.planted_at
        existing.expected_harvest_at = record.expected_harvest_at
        existing.notes = record.notes
        existing.is_deleted = record.is_deleted
        existing.client_updated_at = incoming
        db.add(existing)
        await db.flush()
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=existing.id,
                action=SyncAction.UPDATED,
                server_updated_at=utcnow(),
            ),
            existing.id,
        )

    return (
        SyncRecordResult(
            client_id=record.client_id,
            server_id=existing.id,
            action=SyncAction.SERVER_WINS,
            server_updated_at=ensure_utc(existing.updated_at) or now,
            reason="Server copy is newer than the device copy (last-write-wins).",
            server_record=SyncPlotOut.model_validate(existing).model_dump(mode="json"),
        ),
        existing.id,
    )


async def _resolve_plot_id(
    db: AsyncSession, owner_id: str, plot_client_id: str, plot_map: dict[str, str]
) -> str | None:
    if plot_client_id in plot_map:
        return plot_map[plot_client_id]
    return (
        await db.execute(
            select(Plot.id)
            .join(Farm, Plot.farm_id == Farm.id)
            .where(Farm.owner_id == owner_id, Plot.client_id == plot_client_id)
        )
    ).scalar_one_or_none()


async def _sync_diagnosis(
    db: AsyncSession,
    owner_id: str,
    record: SyncDiagnosisRecord,
    plot_map: dict[str, str],
) -> tuple[SyncRecordResult, str | None]:
    plot_id: str | None = None
    note: str | None = None
    if record.plot_client_id:
        plot_id = await _resolve_plot_id(db, owner_id, record.plot_client_id, plot_map)
        if plot_id is None:
            # Keep the diagnosis (it is valuable) but flag the missing context.
            note = f"plot_not_found: '{record.plot_client_id}' was not synced yet."

    existing = (
        await db.execute(
            select(Diagnosis).where(
                Diagnosis.farmer_id == owner_id, Diagnosis.client_id == record.client_id
            )
        )
    ).scalar_one_or_none()

    incoming = _incoming_time(record.updated_at)
    now = utcnow()

    if existing is None:
        diagnosis = Diagnosis(
            id=new_id(),
            client_id=record.client_id,
            farmer_id=owner_id,
            plot_id=plot_id,
            status=record.status.value,
            stage=2 if record.disease_id else 1,
            crop_id=record.crop_id,
            crop_label=record.crop_label,
            crop_confidence=record.crop_confidence,
            crop_source=record.crop_source,
            disease_id=record.disease_id,
            disease_label=record.disease_label,
            disease_confidence=record.disease_confidence,
            notes=record.notes,
            created_at=ensure_utc(record.created_at) or utcnow(),
            client_updated_at=incoming,
        )
        db.add(diagnosis)
        await db.flush()
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=diagnosis.id,
                action=SyncAction.CREATED,
                server_updated_at=now,
                reason=note,
            ),
            diagnosis.id,
        )

    # Integrity guard: an offline client may never un-process a diagnosis.
    if (
        record.status == DiagnosisStatus.QUEUED
        and existing.status in DiagnosisStatus.protected_from_client_downgrade()
    ):
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=existing.id,
                action=SyncAction.SERVER_WINS,
                server_updated_at=ensure_utc(existing.updated_at) or now,
                reason=(
                    "Diagnosis was already processed on the server "
                    f"(status '{existing.status}'); offline copies cannot reset it."
                ),
                server_record=SyncDiagnosisOut.model_validate(existing).model_dump(mode="json"),
            ),
            existing.id,
        )

    stored = _stored_time(existing.updated_at, existing.client_updated_at)
    if _wins(incoming, stored):
        existing.plot_id = plot_id or existing.plot_id
        existing.status = record.status.value
        existing.stage = 2 if record.disease_id else existing.stage
        existing.crop_id = record.crop_id
        existing.crop_label = record.crop_label
        existing.crop_confidence = record.crop_confidence
        existing.crop_source = record.crop_source
        existing.disease_id = record.disease_id
        existing.disease_label = record.disease_label
        existing.disease_confidence = record.disease_confidence
        existing.notes = record.notes
        existing.client_updated_at = incoming
        db.add(existing)
        await db.flush()
        return (
            SyncRecordResult(
                client_id=record.client_id,
                server_id=existing.id,
                action=SyncAction.UPDATED,
                server_updated_at=utcnow(),
                reason=note,
            ),
            existing.id,
        )

    return (
        SyncRecordResult(
            client_id=record.client_id,
            server_id=existing.id,
            action=SyncAction.SERVER_WINS,
            server_updated_at=ensure_utc(existing.updated_at) or now,
            reason="Server copy is newer than the device copy (last-write-wins).",
            server_record=SyncDiagnosisOut.model_validate(existing).model_dump(mode="json"),
        ),
        existing.id,
    )


# ======================================================================
# Endpoints
# ======================================================================
@router.post("/push", response_model=SyncPushResponse)
async def push(payload: SyncPushRequest, db: DbSession, user: CurrentUser) -> SyncPushResponse:
    """Upload offline-created farms, plots and diagnosis requests."""
    server_time = utcnow()
    farm_map: dict[str, str] = {}
    plot_map: dict[str, str] = {}

    created = updated = conflicts = rejected = 0

    # ---- 1) farms (parents first) -------------------------------------
    farm_results: list[SyncRecordResult] = []
    for record in payload.farms:
        result, server_id = await _sync_farm(db, user.id, record)
        farm_results.append(result)
        if server_id:
            farm_map[record.client_id] = server_id
        if result.action == SyncAction.CREATED:
            created += 1
        elif result.action == SyncAction.UPDATED:
            updated += 1
        elif result.action == SyncAction.SERVER_WINS:
            conflicts += 1
        else:
            rejected += 1

    # ---- 2) plots ------------------------------------------------------
    plot_results: list[SyncRecordResult] = []
    for record in payload.plots:
        result, server_id = await _sync_plot(db, user.id, record, farm_map)
        plot_results.append(result)
        if server_id:
            plot_map[record.client_id] = server_id
        if result.action == SyncAction.CREATED:
            created += 1
        elif result.action == SyncAction.UPDATED:
            updated += 1
        elif result.action == SyncAction.SERVER_WINS:
            conflicts += 1
        else:
            rejected += 1

    # ---- 3) diagnoses --------------------------------------------------
    diagnosis_results: list[SyncRecordResult] = []
    for record in payload.diagnoses:
        result, server_id = await _sync_diagnosis(db, user.id, record, plot_map)
        diagnosis_results.append(result)
        if result.action == SyncAction.CREATED:
            created += 1
        elif result.action == SyncAction.UPDATED:
            updated += 1
        elif result.action == SyncAction.SERVER_WINS:
            conflicts += 1
        else:
            rejected += 1

    await db.commit()  # single atomic transaction for the whole batch

    response = SyncPushResponse(
        farms=farm_results,
        plots=plot_results,
        diagnoses=diagnosis_results,
        created=created,
        updated=updated,
        conflicts=conflicts,
        rejected=rejected,
        server_time=utcnow(),
    )

    # Optional piggy-backed pull: everything the device is missing.
    if payload.pulled_at is not None:
        logger.info(
            "Sync push from %s: +%d ~%d conflicts=%d rejected=%d (pull requested since %s)",
            user.id,
            created,
            updated,
            conflicts,
            rejected,
            payload.pulled_at,
        )
    else:
        logger.info(
            "Sync push from %s: +%d ~%d conflicts=%d rejected=%d",
            user.id,
            created,
            updated,
            conflicts,
            rejected,
        )
    return response


async def _pull_model(
    db: AsyncSession,  # noqa: ANN001 - AsyncSession
    model,  # noqa: ANN001
    owner_condition,  # noqa: ANN001
    since: datetime | None,
    since_id: str | None,
    limit: int,
) -> tuple[list, bool, tuple[datetime, str] | None]:  # noqa: ANN001
    query = select(model).where(owner_condition)
    if since is not None:
        if since_id:
            query = query.where(
                (model.updated_at > since)
                | ((model.updated_at == since) & (model.id > since_id))
            )
        else:
            query = query.where(model.updated_at > since)

    rows = list(
        (
            await db.execute(
                query.order_by(model.updated_at.asc(), model.id.asc()).limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]

    cursor: tuple[datetime, str] | None = None
    if rows:
        last = rows[-1]
        cursor = (ensure_utc(last.updated_at) or utcnow(), last.id)
    return rows, has_more, cursor


@router.get("/pull", response_model=SyncPullResponse)
async def pull(
    db: DbSession,
    user: CurrentUser,
    since: datetime | None = Query(
        None, description="ISO-8601 server cursor (response.next_since of the previous call)"
    ),
    since_id: str | None = Query(None, description="Row id of the previous cursor (tie-breaker)"),
    limit: int = Query(200, ge=1, le=SYNC_BATCH_LIMIT),
) -> SyncPullResponse:
    """Download everything changed since the client's cursor (keyset paging)."""
    since_dt = _incoming_time(since)

    farms, farms_more, farms_cursor = await _pull_model(
        db, Farm, Farm.owner_id == user.id, since_dt, since_id, limit
    )
    plots, plots_more, plots_cursor = await _pull_model(
        db, Plot, Plot.farm_id.in_(select(Farm.id).where(Farm.owner_id == user.id)),
        since_dt, since_id, limit,
    )
    diagnoses, diagnoses_more, diagnoses_cursor = await _pull_model(
        db, Diagnosis, Diagnosis.farmer_id == user.id, since_dt, since_id, limit
    )

    cursors = [c for c in (farms_cursor, plots_cursor, diagnoses_cursor) if c is not None]
    next_cursor = max(cursors) if cursors else None

    return SyncPullResponse(
        farms=[SyncFarmOut.model_validate(row) for row in farms],
        plots=[SyncPlotOut.model_validate(row) for row in plots],
        diagnoses=[SyncDiagnosisOut.model_validate(row) for row in diagnoses],
        server_time=utcnow(),
        has_more=bool(farms_more or plots_more or diagnoses_more),
        next_since=next_cursor[0] if next_cursor else None,
        next_since_id=next_cursor[1] if next_cursor else None,
    )


@router.get("/clock")
async def clock(user: CurrentUser) -> dict[str, str]:
    """Server clock — devices use it to correct drift before LWW pushes."""
    return {"server_time": utcnow().isoformat(), "user_id": user.id}


@router.post("/reset-cursor", status_code=status.HTTP_202_ACCEPTED)
async def reset_cursor() -> dict[str, str]:
    """Documented helper: a device that lost its cursor re-pulls from epoch.

    Records are idempotent by ``client_id``/``id``, so a full re-pull is always
    safe (upsert semantics on the client).
    """
    return {"since": "1970-01-01T00:00:00+00:00", "note": "Pull again with this cursor."}
