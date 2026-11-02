"""Research-only tool schemas and dispatch with server-owned context injection."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import TYPE_CHECKING

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.rag.schemas import CalendarDate
from app.tools.fundamentals import search_fundamentals
from app.tools.stock_data import query_stock_data
from app.tools.strategy_wiki import StrategyWikiArgs

if TYPE_CHECKING:
    from app.agent.schemas import ResearchContext
    from app.core.config import Settings
    from app.rag.service import RagService
    from app.tools.strategy_wiki import StrategyWikiService

LOGGER = logging.getLogger(__name__)


class FundamentalsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000, strict=True)
    top_k: int = Field(default=5, ge=1, le=20, strict=True)

    @field_validator("query")
    @classmethod
    def clean_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value


class WikiArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_evidence: bool = Field(default=False, strict=True)


class MarketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=60, ge=1, le=500, strict=True)
    start_date: CalendarDate | None = None
    end_date: CalendarDate | None = None

    @model_validator(mode="after")
    def validate_range(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("start_date must not be after end_date")
        return self


def _failure(code: str, message: str) -> dict:
    return {
        "ok": False,
        "found": False,
        "error": code,
        "message": message,
        "context": "",
        "citations": {},
    }


class ResearchTools:
    """No I/O during construction. Each execution receives the frozen context."""

    def __init__(
        self, settings: Settings, rag_service: RagService, wiki_service: StrategyWikiService
    ):
        self.settings = settings
        self.rag_service = rag_service
        self.wiki_service = wiki_service

    @staticmethod
    def definitions(context: ResearchContext) -> list[dict]:
        descriptions = [
            (
                "search_fundamentals",
                FundamentalsArgs,
                "检索当前固定标的的基本面原文及引用；公开／形成日受本轮资料截止日约束。"
                "无结果表示资料未检出，不表示公司没有风险。不得自行改变标的或截止日。",
            ),
            (
                "query_stock_data",
                MarketArgs,
                "只读当前固定标的的本地未复权日线，日期不得超出本轮行情截止日。"
                "返回原始OHLC、量额及实际日期；不提供均线、财报指标、收益或买卖信号。",
            ),
        ]
        if context.strategy_id and context.strategy_version:
            descriptions.append(
                (
                    "query_strategy_wiki",
                    WikiArgs,
                    "读取本轮已固定的策略ID和版本的规则原文及引用，不允许切换策略。"
                    "只有需要历史研究证据时开启include_evidence；该证据不证明历史时点可知。",
                )
            )
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": model.model_json_schema(),
                },
            }
            for name, model, description in descriptions
        ]

    def execute(self, name: str, arguments: dict, context: ResearchContext) -> dict:
        """Reject model-supplied scope fields instead of silently overriding them."""
        models = {
            "search_fundamentals": FundamentalsArgs,
            "query_strategy_wiki": WikiArgs,
            "query_stock_data": MarketArgs,
        }
        if name not in models:
            return _failure("tool_not_found", "当前研究没有注册该工具。")
        if name == "query_strategy_wiki" and not (context.strategy_id and context.strategy_version):
            return _failure("tool_unavailable", "本轮尚未固定策略ID和版本，不能查询策略规则。")
        if not isinstance(arguments, dict):
            return _failure("invalid_arguments", "工具参数必须是JSON对象。")
        try:
            args = models[name].model_validate(arguments)
            if name == "search_fundamentals":
                return search_fundamentals(
                    args.query,
                    top_k=args.top_k,
                    symbol=context.symbol,
                    cutoff_date=context.materials_cutoff_date,
                    service=self.rag_service,
                )
            if name == "query_strategy_wiki":
                result = self.wiki_service.query(
                    StrategyWikiArgs(
                        strategy_id=context.strategy_id,
                        version=context.strategy_version,
                        include_evidence=args.include_evidence,
                        max_chars=None,
                    )
                )
                return self._wiki_result(result, context)
            result = query_stock_data(
                context.symbol,
                limit=args.limit,
                start_date=args.start_date,
                end_date=args.end_date,
                cutoff_date=context.market_cutoff_date,
                settings=self.settings,
            )
            return self._market_result(result)
        except ValidationError:
            return _failure("invalid_arguments", "工具参数无效或包含本轮不允许修改的范围字段。")
        except HTTPException:
            return _failure("tool_unavailable", "研究工具当前不可用，请检查后端日志。")
        except Exception as exc:
            LOGGER.warning("Research tool execution failed (%s)", type(exc).__name__)
            return _failure("tool_failed", "研究工具执行失败，请检查后端日志。")

    @staticmethod
    def _wiki_result(result: dict, context: ResearchContext) -> dict:
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return _failure("invalid_tool_response", "策略工具返回了无效结构。")
        result = deepcopy(result)
        result.setdefault("found", False)
        result.setdefault("context", "")
        result.setdefault("citations", {})
        result["scope"] = {
            "strategy_id": context.strategy_id,
            "strategy_version": context.strategy_version,
        }
        return result

    @staticmethod
    def _market_result(result: dict) -> dict:
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return _failure("invalid_tool_response", "行情工具返回了无效结构。")
        result = deepcopy(result)
        snapshot = deepcopy(result)
        result.update(context="", citations={}, snapshot=snapshot)
        result.setdefault("found", False)
        if not result["ok"] or not result["found"]:
            return result
        excerpt = json.dumps(snapshot, ensure_ascii=False, allow_nan=False)
        result["context"] = f"[D1] 来源：{result['source_file']} / 本地日线快照\n{excerpt}"
        result["citations"] = {
            "D1": {
                "citation_id": "D1",
                "source_kind": "market",
                "source": result["source_file"],
                "heading_path": "本地日线快照",
                "symbol": result["symbol"],
                "actual_range": deepcopy(result["actual_range"]),
                "requested_range": deepcopy(result["requested_range"]),
                "units": deepcopy(result["units"]),
                "adjustment": result["adjustment"],
                "source_id": result["source_id"],
                "excerpt": excerpt,
            }
        }
        return result
