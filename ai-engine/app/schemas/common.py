from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceBasis = Literal["explicit", "inferred"]


class StrictSchema(BaseModel):
    """Base model that rejects unknown fields and trims string whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Uncertainty(StrictSchema):
    field: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)
    severity: Literal["low", "medium", "high"] = "medium"


class AnalysisSummary(StrictSchema):
    overview: str = Field(min_length=1, max_length=4000)
    key_points: list[str] = Field(default_factory=list, max_length=20)
    confidence: float = Field(ge=0.0, le=1.0)


class AnalysisMetadata(StrictSchema):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=40)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StructuredExtractionResult(StrictSchema):
    schema_name: str = Field(min_length=1, max_length=120)
    data: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainties: list[Uncertainty] = Field(default_factory=list)
