"""Per-request research execution with a single, lazily created worker.

Timeouts stop waiting; Python threads cannot forcibly stop an in-flight SDK or
file read. The service stays busy until that worker actually finishes.
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from copy import deepcopy
from datetime import UTC, datetime

from app.agent.citations import answer_citations, prepare_evidence
from app.agent.prompts import PROMPT_VERSION, SYSTEM_PROMPT
from app.agent.schemas import ResearchRequest
from app.agent.tools import ResearchTools

LOGGER = logging.getLogger(__name__)
EMPTY_ANSWER = "未检索到可用于本次回答的证据，无法判断。请补充资料、明确已选策略或核对数据范围；未检出不代表不存在风险。"


class ResearchError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def call_model(settings, messages: list[dict], definitions: list[dict], timeout: float) -> dict:
    # Keep SDK import/client creation out of application startup. Disable SDK
    # retries so a paid request is not silently repeated by this adapter.
    from openai import APIError, APITimeoutError, OpenAI

    key = settings.deepseek_api_key
    if not key or not key.get_secret_value().strip():
        raise ResearchError("model_not_configured", "请配置本后端的 DEEPSEEK_API_KEY。")
    try:
        with OpenAI(
            api_key=key.get_secret_value(),
            base_url=str(settings.deepseek_base_url),
            timeout=timeout,
            max_retries=0,
        ) as client:
            response = client.chat.completions.create(
                model=settings.deepseek_model,
                messages=messages,
                tools=definitions,
                stream=False,
                reasoning_effort="none",
            )
            if not response.choices or response.choices[0].finish_reason not in {
                "stop",
                "tool_calls",
            }:
                raise ResearchError("model_response_invalid", "模型未返回完整回答或工具调用。")
            return response.choices[0].message.model_dump(
                include={"role", "content", "tool_calls"}, exclude_none=True
            )
    except APITimeoutError as exc:
        raise ResearchError("model_timeout", "模型请求超时。") from exc
    except APIError as exc:
        LOGGER.warning("Research model failed (%s)", type(exc).__name__)
        raise ResearchError("model_failed", "模型请求失败，请检查后端配置或日志。") from exc


class _Execution:
    def __init__(self, service, request: ResearchRequest, pool: ThreadPoolExecutor):
        self.service, self.request, self.pool = service, request, pool
        self.settings = service.settings
        self.deadline = time.monotonic() + self.settings.agent_total_timeout_seconds
        self.pending = None
        self.model_calls = self.tool_calls = 0
        self.citations, self.snapshots, self.steps = {}, [], []
        self.answer = ""
        self.limitations = []
        self.definitions = service.tools.definitions(request.context)
        self.names = {item["function"]["name"] for item in self.definitions}

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ResearchError("run_timeout", "研究达到总时限，已停止等待。")
        return remaining

    def event(self, step, status, *, tool=None, duration_ms=None, error=None):
        item = {"seq": len(self.steps) + 1, "step": step, "status": status}
        if tool is not None:
            item["tool"] = tool
        if duration_ms is not None:
            item["duration_ms"] = duration_ms
        if error is not None:
            item["error"] = error
        self.steps.append(item)

    def wait(self, function, timeout, timeout_code):
        remaining = self.remaining()
        wait_for = min(timeout, remaining)
        self.pending = self.pool.submit(function, wait_for)
        try:
            result = self.pending.result(timeout=wait_for)
            self.remaining()
            return result
        except FutureTimeout as exc:
            self.pending.cancel()
            code = "run_timeout" if remaining <= timeout else timeout_code
            raise ResearchError(code, "执行超时，已停止等待；后台调用可能仍在结束中。") from exc

    def model_node(self, state):
        self.remaining()
        if self.model_calls >= self.settings.agent_max_model_calls:
            raise ResearchError("max_model_calls_exceeded", "达到模型调用次数上限。")
        self.model_calls += 1
        self.event("model", "running")
        started = time.monotonic()
        try:
            result = self.wait(
                lambda timeout: self.service.model_call(
                    deepcopy(state["messages"]), deepcopy(self.definitions), timeout
                ),
                self.settings.agent_model_timeout_seconds,
                "model_timeout",
            )
            from openai.types.chat import ChatCompletionMessage

            message = ChatCompletionMessage.model_validate(result).model_dump(
                include={"role", "content", "tool_calls"}, exclude_none=True
            )
            calls = message.get("tool_calls", [])
            if calls:
                if any(call.get("type") != "function" for call in calls):
                    raise ValueError("Only function tools are supported")
                ids = [call["id"] for call in calls]
                if not all(ids) or len(set(ids)) != len(ids):
                    raise ValueError("Tool IDs must be nonempty and unique per batch")
            elif not isinstance(message.get("content"), str) or not message["content"].strip():
                raise ValueError("The final response is empty")
            self.event("model", "completed", duration_ms=round((time.monotonic() - started) * 1000))
            return {"messages": [message], "model_calls": self.model_calls}
        except ResearchError as exc:
            self.event("model", "failed", error=exc.code)
            raise
        except (ValueError, TypeError, KeyError) as exc:
            self.event("model", "failed", error="model_response_invalid")
            raise ResearchError("model_response_invalid", "模型响应结构无效。") from exc
        except Exception as exc:
            self.event("model", "failed", error="model_failed")
            LOGGER.warning("Research model adapter failed (%s)", type(exc).__name__)
            raise ResearchError("model_failed", "模型请求失败，请检查后端日志。") from exc

    def tool_node(self, state):
        messages = []
        for call in state["messages"][-1]["tool_calls"]:
            self.remaining()
            if self.tool_calls >= self.settings.agent_max_tool_calls:
                raise ResearchError("max_tool_calls_exceeded", "达到工具调用次数上限。")
            self.tool_calls += 1
            name = call["function"]["name"]
            self.event("tool", "running", tool=name)
            started = time.monotonic()
            arguments = None
            result = None
            try:
                if name not in self.names:
                    raise ResearchError("unknown_tool", "模型调用了未启用的工具。")
                try:
                    arguments = json.loads(
                        call["function"]["arguments"],
                        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                    )
                except (ValueError, TypeError) as exc:
                    raise ResearchError(
                        "invalid_arguments", "工具参数必须是合法JSON对象。"
                    ) from exc
                if not isinstance(arguments, dict):
                    raise ResearchError("invalid_arguments", "工具参数必须是JSON对象。")
                result = self.wait(
                    lambda timeout, name=name, arguments=arguments: self.service.tools.execute(
                        name, arguments, self.request.context
                    ),
                    self.settings.agent_tool_timeout_seconds,
                    "tool_timeout",
                )
                if not isinstance(result, dict) or result.get("ok") is not True:
                    if isinstance(result, dict) and result.get("error") == "invalid_arguments":
                        raise ResearchError("invalid_arguments", "工具参数未通过校验。")
                    raise ResearchError("tool_failed", "研究工具执行失败，请查看本次工具记录。")
                try:
                    result = prepare_evidence(result, self.tool_calls, name)
                except (ValueError, TypeError) as exc:
                    raise ResearchError("tool_result_invalid", "工具证据结构不完整。") from exc
                # Copy only completed results into the run; a timed-out worker
                # cannot later mutate the answer, snapshots or citation map.
                self.citations.update(result.get("citations", {}))
                self.snapshots.append(
                    {"call_id": call["id"], "tool": name, "arguments": arguments, "result": result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "content": json.dumps(result, ensure_ascii=False, allow_nan=False),
                    }
                )
                self.event(
                    "tool",
                    "completed",
                    tool=name,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
            except ResearchError as exc:
                self.snapshots.append(
                    {
                        "call_id": call["id"],
                        "tool": name,
                        "arguments": arguments,
                        "result": result
                        if isinstance(result, dict)
                        else {
                            "ok": False,
                            "error": exc.code,
                            "message": exc.message,
                        },
                    }
                )
                self.event("tool", "failed", tool=name, error=exc.code)
                raise
            except Exception as exc:
                self.snapshots.append(
                    {
                        "call_id": call["id"],
                        "tool": name,
                        "arguments": arguments,
                        "result": {"ok": False, "error": "tool_failed"},
                    }
                )
                self.event("tool", "failed", tool=name, error="tool_failed")
                LOGGER.warning("Research tool adapter failed (%s)", type(exc).__name__)
                raise ResearchError("tool_failed", "研究工具执行失败，请检查后端日志。") from exc
        return {
            "messages": messages,
            "tool_calls": self.tool_calls,
            "citations": deepcopy(self.citations),
        }

    def validate_node(self, state):
        self.remaining()
        if not self.tool_calls:
            raise ResearchError("no_tool_calls", "回答未经过本轮实际工具查证。")
        if not self.citations:
            self.answer = EMPTY_ANSWER
            self.limitations = ["no_evidence"]
        else:
            answer = state["messages"][-1].get("content", "")
            references = answer_citations(answer)
            if not references or references - self.citations.keys():
                raise ResearchError(
                    "citation_validation_failed", "回答引用缺失或编号不属于本轮证据。"
                )
            self.answer = answer
        self.event("validate", "completed")
        return {"citations": deepcopy(self.citations)}


class ResearchAgentService:
    def __init__(self, settings, rag_service, wiki_service, *, model_call=None, tools=None):
        self.settings = settings
        self.tools = (
            tools if tools is not None else ResearchTools(settings, rag_service, wiki_service)
        )
        self.model_call = (
            model_call
            if model_call is not None
            else (
                lambda messages, definitions, timeout: call_model(
                    settings, messages, definitions, timeout
                )
            )
        )
        self._gate = threading.BoundedSemaphore(1)

    def run(self, request: ResearchRequest) -> dict:
        result = {
            "run_id": uuid.uuid4().hex,
            "status": "failed",
            "question": request.question,
            "context": request.context.model_dump(mode="json"),
            "started_at": datetime.now(UTC).isoformat(),
            "finished_at": None,
            "model": self.settings.deepseek_model,
            "prompt_version": PROMPT_VERSION,
            "answer": "",
            "citations": {},
            "tool_snapshots": [],
            "steps": [],
            "model_calls": 0,
            "tool_calls": 0,
            "limitations": [],
            "error": None,
        }
        if not self._gate.acquire(blocking=False):
            result.update(
                error={
                    "code": "research_busy",
                    "message": "另一次研究或后台调用尚未结束，请稍后再试。",
                },
                finished_at=datetime.now(UTC).isoformat(),
            )
            return result
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research")
        execution = None
        try:
            from langsmith import tracing_context

            from app.agent.graph import build_graph

            execution = _Execution(self, request, pool)
            graph = build_graph(execution.model_node, execution.tool_node, execution.validate_node)
            system = (
                SYSTEM_PROMPT
                + "\n服务端冻结的研究条件："
                + json.dumps(result["context"], ensure_ascii=False)
            )
            with tracing_context(enabled=False):
                graph.invoke(
                    {
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": request.question},
                        ],
                        "model_calls": 0,
                        "tool_calls": 0,
                        "citations": {},
                    },
                    config={"recursion_limit": 2 * self.settings.agent_max_model_calls + 4},
                )
            result.update(status="success", answer=execution.answer)
        except ResearchError as exc:
            result["error"] = {"code": exc.code, "message": exc.message}
        except Exception as exc:
            LOGGER.warning("Research run failed (%s)", type(exc).__name__)
            result["error"] = {
                "code": "research_failed",
                "message": "研究执行失败，请检查后端日志。",
            }
        finally:
            if execution is not None:
                result.update(
                    citations=execution.citations,
                    tool_snapshots=execution.snapshots,
                    steps=execution.steps,
                    model_calls=execution.model_calls,
                    tool_calls=execution.tool_calls,
                    limitations=execution.limitations,
                )
            result["finished_at"] = datetime.now(UTC).isoformat()
            pool.shutdown(wait=False, cancel_futures=True)
            if (
                execution is not None
                and execution.pending is not None
                and not execution.pending.done()
            ):
                execution.pending.add_done_callback(lambda future: self._gate.release())
            else:
                self._gate.release()
        return result
