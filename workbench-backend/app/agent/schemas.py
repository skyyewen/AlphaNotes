"""Research inputs are separate from tool arguments and never model-editable."""

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.rag.schemas import CalendarDate


class ResearchContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    symbol: str
    instrument_type: Literal["stock"] = "stock"
    materials_cutoff_date: CalendarDate | None = None
    market_cutoff_date: CalendarDate | None = None
    strategy_id: str | None = None
    strategy_version: str | None = None

    @field_validator("symbol", mode="before")
    @classmethod
    def validate_symbol(cls, value):
        if not isinstance(value, str):
            raise ValueError("symbol must be a six-digit stock code with optional exchange")
        value = value.strip().upper()
        if not re.fullmatch(r"[0-9]{6}(?:\.(?:SH|SZ|BJ))?", value):
            raise ValueError("symbol must be a six-digit stock code with optional .SH/.SZ/.BJ")
        return value

    @field_validator("strategy_id", "strategy_version", mode="before")
    @classmethod
    def validate_identifier(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", value
        ):
            raise ValueError("use a registered strategy ID/version")
        return value

    @model_validator(mode="after")
    def paired_strategy(self) -> Self:
        if bool(self.strategy_id) != bool(self.strategy_version):
            raise ValueError("strategy_id and strategy_version must be supplied together")
        return self


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question: str = Field(min_length=1, max_length=4000)
    context: ResearchContext

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question cannot be blank")
        return value
