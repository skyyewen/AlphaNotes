"""Namespace actual evidence without rewriting bracketed text inside sources."""

import json
import re
from copy import deepcopy


def prepare_evidence(result: dict, call_number: int, tool_name: str) -> dict:
    value = deepcopy(result)
    if value.get("ok") is not True:
        return value
    if not isinstance(value.get("found"), bool):
        raise ValueError("Tool result must declare found")
    if not value["found"]:
        if value.get("citations") or value.get("context"):
            raise ValueError("An empty result must not carry evidence")
        return value
    originals = value.get("citations")
    if not isinstance(originals, dict) or not originals:
        raise ValueError("Evidence needs structured citations")
    citations, blocks = {}, []
    for number, (original_id, item) in enumerate(originals.items(), 1):
        if not isinstance(item, dict) or not isinstance(item.get("excerpt"), str):
            raise ValueError("A citation must preserve its actual excerpt")
        if not item["excerpt"].strip():
            raise ValueError("A citation cannot have empty evidence")
        citation_id = f"R{call_number}-{number}"
        citation = {
            **item,
            "original_citation_id": str(original_id),
            "citation_id": citation_id,
            "tool": tool_name,
        }
        citations[citation_id] = citation
        metadata = {key: val for key, val in citation.items() if key != "excerpt"}
        blocks.append(
            f"[{citation_id}] 来源：{citation.get('source', tool_name)}\n"
            f"元数据：{json.dumps(metadata, ensure_ascii=False)}\n{citation['excerpt']}"
        )
    value.update(context="\n\n---\n\n".join(blocks), citations=citations)
    return value


def answer_citations(answer: str) -> set[str]:
    # Inline/code blocks are not reference syntax. Citation existence does not
    # establish semantic support; that needs separately reviewed real cases.
    text = re.sub(r"```[\s\S]*?```|`[^`\n]*`", "", answer)
    referenced = set()
    for match in re.finditer(r"\[([^\]\n]+)\]", text):
        label = match.group(1).strip()
        if re.search(r"R\d", label):
            referenced.update(re.findall(r"R\d+(?:-[A-Za-z0-9_-]+)?", label))
        elif re.fullmatch(r"[WD]\d+", label):
            referenced.add(label)
        elif re.fullmatch(r"\d+", label):
            if not (match.start() and re.match(r"[A-Za-z0-9_]", text[match.start() - 1])):
                referenced.add(label)
    return referenced
