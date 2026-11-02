"""Exercise graph behavior with isolated model/tool boundaries, never real APIs."""

import copy
import json
import threading
from collections import deque

import pytest

from app.agent.schemas import ResearchRequest
from app.agent.service import ResearchAgentService
from app.core.config import Settings


def tool_call(name="search_fundamentals", *, call_id="call-1", arguments=None):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments or {"query": "收入"}, ensure_ascii=False),
                },
            }
        ],
    }


def answer(text="资料显示收入增长。[R1-1]"):
    return {"role": "assistant", "content": text}


def evidence(*, wiki=False):
    citation_id = "W1" if wiki else "1"
    source = "策略.md" if wiki else "财报.md"
    body = "规则要求核对证据。" if wiki else "收入同比增长。"
    body += "正文中的列表[1]只是原文索引。"
    return {
        "ok": True,
        "found": True,
        "context": f"[{citation_id}] 来源：{source} / 正文\n{body}",
        "citations": {
            citation_id: {
                "citation_id": citation_id,
                "source": source,
                "document_id": "strategy-v1" if wiki else "report-1",
                "heading_path": "正文",
                "excerpt": body,
                "available_date": None if wiki else "2026-10-01",
            }
        },
    }


class FakeTools:
    def __init__(self, results=None, execute=None):
        self.results = deque(results or [evidence()])
        self.calls = []
        self.execute_override = execute

    def definitions(self, context):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "Isolated read-only test tool.",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            }
            for name in ("search_fundamentals", "query_strategy_wiki")
        ]

    def execute(self, name, arguments, context):
        self.calls.append((name, copy.deepcopy(arguments), context))
        if self.execute_override:
            return self.execute_override(name, arguments, context)
        return copy.deepcopy(self.results.popleft())


class FakeModel:
    def __init__(self, responses):
        self.responses = deque(responses)
        self.calls = []

    def __call__(self, messages, definitions, timeout):
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(definitions), timeout))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


@pytest.fixture
def research_request():
    return ResearchRequest(
        question="核对收入与规则证据",
        context={
            "symbol": "601318.SH",
            "materials_cutoff_date": "2026-10-01",
            "market_cutoff_date": "2026-10-13",
            "strategy_id": "weekly_platform",
            "strategy_version": "X2",
        },
    )


def make_service(tmp_path, responses, *, tools=None, **overrides):
    settings = Settings(
        _env_file=None,
        rag_data_dir=tmp_path / "rag",
        stock_daily_parquet_path=tmp_path / "market.parquet",
        strategy_wiki_root=tmp_path / "wiki",
        **overrides,
    )
    model = responses if callable(responses) else FakeModel(responses)
    tools = tools if tools is not None else FakeTools()
    service = ResearchAgentService(settings, None, None, model_call=model, tools=tools)
    return service, model, tools


def assert_failed(result, code):
    assert result["status"] == "failed"
    assert result["answer"] == ""
    assert result["error"]["code"] == code
    assert result["run_id"]
    assert result["finished_at"]


def test_real_graph_owns_multiround_messages_and_preserves_context(tmp_path, research_request):
    tools = FakeTools([evidence(), evidence(wiki=True)])
    service, model, tools = make_service(
        tmp_path,
        [
            tool_call(call_id="first"),
            tool_call("query_strategy_wiki", call_id="second"),
            answer("财报与规则需分别核对。[R1-1][R2-1]"),
        ],
        tools=tools,
    )
    result = service.run(research_request)
    assert result["status"] == "success"
    assert result["error"] is None
    assert set(result["citations"]) == {"R1-1", "R2-1"}
    assert result["model_calls"] == 3
    assert result["tool_calls"] == 2
    assert result["context"] == research_request.context.model_dump(mode="json")
    assert result["started_at"] <= result["finished_at"]
    assert "messages" not in result
    final_input = model.calls[-1][0]
    assert [m["tool_call_id"] for m in final_input if m["role"] == "tool"] == ["first", "second"]
    assert sum(m["role"] == "user" for m in final_input) == 1
    assert [call["id"] for message in final_input for call in message.get("tool_calls", [])] == [
        "first",
        "second",
    ]
    tool_results = [json.loads(m["content"]) for m in final_input if m["role"] == "tool"]
    assert set(tool_results[0]["citations"]) == {"R1-1"}
    assert set(tool_results[1]["citations"]) == {"R2-1"}
    assert all("正文中的列表[1]只是原文索引。" in item["context"] for item in tool_results)
    assert all(call[2] == research_request.context for call in tools.calls)
    assert len(result["tool_snapshots"]) == 2
    assert result["steps"]


def test_independent_runs_do_not_reuse_prior_messages_or_citations(tmp_path, research_request):
    service, model, _ = make_service(
        tmp_path,
        [tool_call(call_id="a"), answer(), tool_call(call_id="b"), answer()],
        tools=FakeTools([evidence(), evidence()]),
    )
    first = service.run(research_request)
    second = service.run(research_request.model_copy(update={"question": "另一轮问题"}))
    assert first["status"] == second["status"] == "success"
    assert first["run_id"] != second["run_id"]
    assert set(second["citations"]) == {"R1-1"}
    second_initial = model.calls[2][0]
    assert not any(m["role"] == "tool" for m in second_initial)
    assert not any(m.get("tool_calls") for m in second_initial)
    assert [m["content"] for m in second_initial if m["role"] == "user"] == ["另一轮问题"]


@pytest.mark.parametrize(
    "text",
    ["没有任何引用的事实结论。", "使用了不存在的来源。[R7-9]", "旧编号不能冒充当前引用。[1]"],
)
def test_missing_or_fabricated_citations_do_not_escape_as_success(tmp_path, research_request, text):
    service, _, _ = make_service(tmp_path, [tool_call(), answer(text)])
    assert_failed(service.run(research_request), "citation_validation_failed")


@pytest.mark.parametrize("legacy_id", ["W1", "D1"])
def test_valid_reference_does_not_hide_an_additional_legacy_citation(
    tmp_path, research_request, legacy_id
):
    service, _, _ = make_service(
        tmp_path, [tool_call(), answer(f"已核对财报。[R1-1]另一个来源仍用旧编号。[{legacy_id}]")]
    )
    result = service.run(research_request)
    assert set(result["citations"]) == {"R1-1"}
    assert_failed(result, "citation_validation_failed")


def test_final_answer_without_any_actual_tool_call_fails(tmp_path, research_request):
    service, _, tools = make_service(tmp_path, [answer()])
    result = service.run(research_request)
    assert_failed(result, "no_tool_calls")
    assert not tools.calls


def test_normal_no_hit_stops_with_explicit_limitations_instead_of_model_memory(
    tmp_path, research_request
):
    tools = FakeTools([{"ok": True, "found": False, "context": "", "citations": {}}])
    service, model, _ = make_service(
        tmp_path, [tool_call(), answer("根据我记忆，公司收入非常好。")], tools=tools
    )
    result = service.run(research_request)
    assert result["status"] == "success"
    assert result["error"] is None
    assert "未检索到" in result["answer"]
    assert "no_evidence" in result["limitations"]
    assert result["citations"] == {}
    assert "根据我记忆" not in result["answer"]
    assert len(model.calls) == 2


def test_model_can_retry_an_empty_retrieval_before_answering_with_actual_evidence(
    tmp_path, research_request
):
    tools = FakeTools([{"ok": True, "found": False, "context": "", "citations": {}}, evidence()])
    service, _, _ = make_service(
        tmp_path,
        [tool_call(call_id="empty"), tool_call(call_id="retry"), answer("有后续证据。[R2-1]")],
        tools=tools,
    )
    result = service.run(research_request)
    assert result["status"] == "success"
    assert set(result["citations"]) == {"R2-1"}
    assert not result["limitations"]


def test_tool_failure_is_closed_and_preserves_underlying_failure(tmp_path, research_request):
    tools = FakeTools(
        [{"ok": False, "error": "rerank_failed", "message": "重排服务不可用", "citations": {}}]
    )
    service, model, _ = make_service(tmp_path, [tool_call()], tools=tools)
    result = service.run(research_request)
    assert_failed(result, "tool_failed")
    assert "rerank_failed" in json.dumps(result["tool_snapshots"], ensure_ascii=False)
    assert len(model.calls) == 1


def test_later_tool_failure_cannot_turn_prior_evidence_into_a_successful_answer(
    tmp_path, research_request
):
    tools = FakeTools(
        [evidence(), {"ok": False, "error": "source_changed", "message": "策略来源已变化"}]
    )
    service, model, _ = make_service(
        tmp_path,
        [tool_call(call_id="one"), tool_call("query_strategy_wiki", call_id="two")],
        tools=tools,
    )
    result = service.run(research_request)
    assert_failed(result, "tool_failed")
    assert len(model.calls) == 2
    assert "source_changed" in json.dumps(result["tool_snapshots"], ensure_ascii=False)


def test_agent_does_not_reintroduce_the_old_tool_result_character_cap(tmp_path, research_request):
    original = evidence()
    full_text = "完整证据，不能由Agent二次截断。" * 1500
    original["context"] += full_text
    original["citations"]["1"]["excerpt"] += full_text
    service, model, _ = make_service(tmp_path, [tool_call(), answer()], tools=FakeTools([original]))
    result = service.run(research_request)
    assert result["status"] == "success"
    tools_input = next(message for message in model.calls[-1][0] if message["role"] == "tool")
    assert full_text in json.loads(tools_input["content"])["context"]
    assert full_text in result["citations"]["R1-1"]["excerpt"]


def test_unknown_tool_is_rejected_before_dispatch(tmp_path, research_request):
    service, _, tools = make_service(tmp_path, [tool_call("read_arbitrary_file")])
    assert_failed(service.run(research_request), "unknown_tool")
    assert not tools.calls


def test_malformed_tool_arguments_are_rejected_before_dispatch(tmp_path, research_request):
    message = tool_call()
    message["tool_calls"][0]["function"]["arguments"] = '{"query":'
    service, _, tools = make_service(tmp_path, [message])
    assert_failed(service.run(research_request), "invalid_arguments")
    assert not tools.calls


@pytest.mark.parametrize("message", [{}, {"role": "tool", "content": "wrong role"}])
def test_invalid_model_envelope_fails_explicitly(tmp_path, research_request, message):
    service, _, _ = make_service(tmp_path, [message])
    assert_failed(service.run(research_request), "model_response_invalid")


def test_duplicate_tool_call_ids_in_one_model_batch_are_rejected_before_dispatch(
    tmp_path, research_request
):
    message = tool_call(call_id="duplicate")
    message["tool_calls"].extend(
        tool_call("query_strategy_wiki", call_id="duplicate")["tool_calls"]
    )
    service, model, tools = make_service(tmp_path, [message])
    result = service.run(research_request)
    assert_failed(result, "model_response_invalid")
    assert len(model.calls) == 1
    assert not tools.calls
    assert result["tool_calls"] == 0


@pytest.mark.parametrize("failing_boundary", ["model", "tool"])
def test_unexpected_adapter_exception_closes_step_and_keeps_raw_error_private(
    tmp_path, research_request, failing_boundary
):
    private_error = "private adapter failure F:/private/not-for-response bearer-test-secret"
    if failing_boundary == "model":
        service, _, _ = make_service(tmp_path, [RuntimeError(private_error)])
    else:

        def fail_tool(name, arguments, context):
            raise RuntimeError(private_error)

        service, _, _ = make_service(tmp_path, [tool_call()], tools=FakeTools(execute=fail_tool))
    result = service.run(research_request)
    assert_failed(result, f"{failing_boundary}_failed")
    boundary_steps = [step for step in result["steps"] if step["step"] == failing_boundary]
    assert boundary_steps[0]["status"] == "running"
    assert boundary_steps[-1]["status"] == "failed"
    assert boundary_steps[-1]["error"] == f"{failing_boundary}_failed"
    assert private_error not in json.dumps(result, ensure_ascii=False)


def test_model_call_limit_ends_a_repeated_tool_loop(tmp_path, research_request):
    service, model, _ = make_service(
        tmp_path,
        [tool_call(call_id="one"), tool_call(call_id="two")],
        tools=FakeTools([evidence(), evidence()]),
        agent_max_model_calls=2,
    )
    result = service.run(research_request)
    assert_failed(result, "max_model_calls_exceeded")
    assert len(model.calls) == 2


def test_tool_call_limit_is_checked_before_an_additional_tool_executes(tmp_path, research_request):
    message = tool_call(call_id="one")
    second = tool_call("query_strategy_wiki", call_id="two")["tool_calls"][0]
    message["tool_calls"].append(second)
    service, _, tools = make_service(
        tmp_path,
        [message],
        tools=FakeTools([evidence(), evidence(wiki=True)]),
        agent_max_tool_calls=1,
    )
    result = service.run(research_request)
    assert_failed(result, "max_tool_calls_exceeded")
    assert len(tools.calls) <= 1


def test_model_timeout_does_not_return_a_partial_answer(tmp_path, research_request):
    service, _, _ = make_service(tmp_path, [TimeoutError("private transport timeout")])
    assert_failed(service.run(research_request), "model_timeout")


def test_tool_timeout_keeps_failure_distinct_and_releases_test_worker(tmp_path, research_request):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def execute(name, arguments, context):
        entered.set()
        try:
            release.wait(timeout=3)
            return evidence()
        finally:
            finished.set()

    service, _, _ = make_service(
        tmp_path,
        [tool_call()],
        tools=FakeTools(execute=execute),
        agent_tool_timeout_seconds=0.03,
    )
    try:
        result = service.run(research_request)
        assert entered.is_set()
        assert_failed(result, "tool_timeout")
        frozen_failure = copy.deepcopy(result)
        # Stopping the wait has not terminated the underlying tool. The gate
        # must prevent overlapping work while the test worker is still blocked.
        assert_failed(service.run(research_request), "research_busy")
    finally:
        release.set()
        assert finished.wait(timeout=2)
    assert result == frozen_failure


def test_total_deadline_is_distinct_from_individual_model_timeout(tmp_path, research_request):
    release, finished = threading.Event(), threading.Event()

    def model(messages, definitions, timeout):
        try:
            release.wait(timeout=3)
            return tool_call()
        finally:
            finished.set()

    service, _, _ = make_service(
        tmp_path,
        model,
        agent_total_timeout_seconds=0.03,
        agent_model_timeout_seconds=1,
    )
    try:
        assert_failed(service.run(research_request), "run_timeout")
    finally:
        release.set()
        assert finished.wait(timeout=2)


def test_concurrent_run_is_rejected_without_starting_another_model(tmp_path, research_request):
    entered, release = threading.Event(), threading.Event()
    responses = deque([tool_call(), answer()])
    calls = []
    result_holder = []

    def model(messages, definitions, timeout):
        calls.append(copy.deepcopy(messages))
        if len(calls) == 1:
            entered.set()
            release.wait(timeout=3)
        return responses.popleft()

    service, _, _ = make_service(tmp_path, model)
    worker = threading.Thread(target=lambda: result_holder.append(service.run(research_request)))
    worker.start()
    try:
        assert entered.wait(timeout=2)
        rejected = service.run(research_request)
        assert_failed(rejected, "research_busy")
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(timeout=3)
        assert not worker.is_alive()
    assert result_holder[0]["status"] == "success"
