"""B-owned inspection wire types; offsets are Unicode codepoints, end exclusive."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PIIType = Literal[
    "KR_RRN", "CREDIT_CARD", "API_KEY", "PRIVATE_KEY", "PERSON", "EMAIL_ADDRESS",
    "KR_PHONE_NUMBER", "KR_BANK_ACCOUNT", "KR_LOCATION", "DATE_OF_BIRTH",
    "KR_DRIVER_LICENSE", "KR_PASSPORT", "KR_NHIS", "KR_VEHICLE_PLATE", "USERNAME",
    "PERSONAL_ATTRIBUTE", "ORGANIZATION", "URL", "IP_ADDRESS", "NRP",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class Segment(StrictModel):
    segment_id: str = Field(min_length=1, max_length=128)
    text: str


class InspectionRequest(StrictModel):
    schema_version: Literal["1.0"]
    request_id: str = Field(min_length=1, max_length=128)
    inspection_id: str = Field(min_length=1, max_length=128)
    direction: Literal["outbound", "inbound"]
    segments: list[Segment] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def unique_segment_ids(self):
        ids = [segment.segment_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate segment IDs")
        return self


class Finding(StrictModel):
    segment_id: str = Field(min_length=1, max_length=128)
    type: PIIType
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    detector: Literal["model", "rule"]

    @model_validator(mode="after")
    def nonempty_span(self):
        if self.start >= self.end:
            raise ValueError("invalid span")
        return self


class BackendResult(StrictModel):
    status: Literal["complete", "incomplete"] = "complete"
    findings: list[Finding] = Field(default_factory=list)


class InspectionResponse(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    request_id: str
    inspection_id: str
    status: Literal["complete", "incomplete", "error"]
    findings: list[Finding]
    model_version: str
    threshold_version: str
    timing_ms: float = Field(ge=0, allow_inf_nan=False)
    timing_breakdown_ms: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]]
    error_code: str | None = None


class ApiError(StrictModel):
    status: Literal["error"] = "error"
    error_code: str


def inspection_contract() -> dict:
    """Publish request/response/error schemas from the same wire definitions."""
    definitions = {}
    for model in (InspectionRequest, InspectionResponse, ApiError):
        schema = model.model_json_schema()
        definitions.update(schema.pop("$defs", {}))
        definitions[model.__name__] = schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:ai-pii-gateway:inspection:1.0",
        "title": "B-owned inspection API v1.0",
        "description": "POST /v1/inspect. Unicode codepoint offsets, end exclusive. Only complete responses authorize policy evaluation.",
        "x-owner": "B", "x-max-total-codepoints": 16000,
        "$defs": definitions,
        "oneOf": [{"$ref": f"#/$defs/{model.__name__}"} for model in (InspectionRequest, InspectionResponse, ApiError)],
    }
