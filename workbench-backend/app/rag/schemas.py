"""Small document scope contract: symbol, source kind and calendar availability."""

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator

SourceType = Literal["disclosure", "research_report", "personal_note", "ai_generated"]


def normalize_symbol(value):
    if not isinstance(value, str):
        raise ValueError("symbol must be a security code")
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", value):
        raise ValueError(
            "symbol must contain 1-32 code characters: A-Z, 0-9, dot, underscore or dash"
        )
    return value


def calendar_date(value):
    # Do not silently accept timestamps or derive a public date from a report period.
    if type(value) is date:
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD")
    return date.fromisoformat(value)


CalendarDate = Annotated[date, BeforeValidator(calendar_date)]


class UploadRequest(BaseModel):
    filename: str
    content: str
    symbol: str
    source_type: SourceType
    available_date: CalendarDate | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value):
        return normalize_symbol(value)


class ChatRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    document_id: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    symbol: str | None = None
    cutoff_date: CalendarDate | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value):
        return None if value is None else normalize_symbol(value)

    @field_validator("query")
    @classmethod
    def validate_query(cls, query: str) -> str:
        query = query.strip()
        if not query:
            raise ValueError("query cannot be blank")
        return query


class RetrieveRequest(ChatRequest):
    """Retrieval only: no generation model is invoked."""
