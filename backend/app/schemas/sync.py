"""Schemas for the offline-first sync engine (last-write-wins).

Every record pushed from a device carries:

* ``client_id``   — device-generated UUID, idempotency key (never reassigned)
* ``updated_at``  — device timestamp of the last local modification

The server applies **last-write-wins**: the record with the newest
``updated_at`` becomes canonical. When the server keeps its own copy it is
returned inside a ``server_wins`` result so the client can reconcile.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.diagnosis import DiagnosisStatus

SYNC_BATCH_LIMIT = 500


class SyncFarmRecord(BaseModel):
    client_id: str = Field(min_length=6, max_length=64)
    updated_at: datetime
    name: str = Field(min_length=1, max_length=120)
    location_text: str = Field(default="", max_length=255)
    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    area_hectares: float | None = Field(default=None, ge=0.0, le=1_000_000.0)
    main_crop: str = Field(default="", max_length=80)
    notes: str = Field(default="", max_length=8000)
    is_deleted: bool = False

    @field_validator("name", mode="after")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Farm name must not be empty")
        return cleaned


class SyncPlotRecord(BaseModel):
    client_id: str = Field(min_length=6, max_length=64)
    farm_client_id: str = Field(min_length=6, max_length=64)
    updated_at: datetime
    name: str = Field(min_length=1, max_length=120)
    area_hectares: float | None = Field(default=None, ge=0.0, le=1_000_000.0)
    current_crop: str = Field(default="", max_length=80)
    soil_type: str = Field(default="", max_length=60)
    irrigation_method: str = Field(default="", max_length=60)
    planted_at: date | None = None
    expected_harvest_at: date | None = None
    notes: str = Field(default="", max_length=8000)
    is_deleted: bool = False

    @field_validator("name", mode="after")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Plot name must not be empty")
        return cleaned


class SyncDiagnosisRecord(BaseModel):
    """A diagnosis created while offline (usually with ``status='queued'``).

    After the push, the farmer uploads the actual image through
    ``POST /crop-doctor/diagnose`` using the same ``client_id`` so the offline
    record is completed instead of duplicated.
    """

    client_id: str = Field(min_length=6, max_length=64)
    updated_at: datetime
    created_at: datetime | None = None
    plot_client_id: str | None = Field(default=None, max_length=64)
    status: DiagnosisStatus = DiagnosisStatus.QUEUED

    crop_id: str | None = Field(default=None, max_length=120)
    crop_label: str | None = Field(default=None, max_length=160)
    crop_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    crop_source: str | None = Field(default=None, max_length=32)
    disease_id: str | None = Field(default=None, max_length=120)
    disease_label: str | None = Field(default=None, max_length=200)
    disease_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    notes: str = Field(default="", max_length=8000)


class SyncPushRequest(BaseModel):
    """Batch upload of everything the device created/edited while offline."""

    farms: list[SyncFarmRecord] = Field(default_factory=list, max_length=SYNC_BATCH_LIMIT)
    plots: list[SyncPlotRecord] = Field(default_factory=list, max_length=SYNC_BATCH_LIMIT)
    diagnoses: list[SyncDiagnosisRecord] = Field(default_factory=list, max_length=SYNC_BATCH_LIMIT)
    #: Server cursor the device already has — the response includes everything
    #: newer than this, so a single round-trip performs push **and** pull.
    pulled_at: datetime | None = None


class SyncAction(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    #: Server copy is newer → device must overwrite its local value.
    SERVER_WINS = "server_wins"
    REJECTED = "rejected"


class SyncRecordResult(BaseModel):
    client_id: str
    server_id: str | None = None
    action: SyncAction
    server_updated_at: datetime
    #: Populated for ``server_wins`` (server copy) and ``rejected`` (why).
    reason: str | None = None
    server_record: dict | None = None


class SyncPushResponse(BaseModel):
    farms: list[SyncRecordResult] = Field(default_factory=list)
    plots: list[SyncRecordResult] = Field(default_factory=list)
    diagnoses: list[SyncRecordResult] = Field(default_factory=list)
    created: int = 0
    updated: int = 0
    conflicts: int = 0
    rejected: int = 0
    server_time: datetime


class SyncFarmOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str | None = None
    name: str
    location_text: str = ""
    latitude: float | None = None
    longitude: float | None = None
    area_hectares: float | None = None
    main_crop: str = ""
    notes: str = ""
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class SyncPlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str | None = None
    farm_id: str
    name: str
    area_hectares: float | None = None
    current_crop: str = ""
    soil_type: str = ""
    irrigation_method: str = ""
    planted_at: date | None = None
    expected_harvest_at: date | None = None
    notes: str = ""
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class SyncDiagnosisOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    client_id: str | None = None
    plot_id: str | None = None
    status: DiagnosisStatus
    stage: int = 1
    crop_id: str | None = None
    crop_label: str | None = None
    crop_confidence: float | None = None
    crop_source: str | None = None
    disease_id: str | None = None
    disease_label: str | None = None
    disease_confidence: float | None = None
    notes: str = ""
    created_at: datetime
    updated_at: datetime


class SyncPullQuery(BaseModel):
    since: datetime | None = None
    since_id: str | None = None
    limit: int = Field(default=200, ge=1, le=SYNC_BATCH_LIMIT)


class SyncPullResponse(BaseModel):
    farms: list[SyncFarmOut] = Field(default_factory=list)
    plots: list[SyncPlotOut] = Field(default_factory=list)
    diagnoses: list[SyncDiagnosisOut] = Field(default_factory=list)
    server_time: datetime
    has_more: bool = False
    next_since: datetime | None = None
    next_since_id: str | None = None
