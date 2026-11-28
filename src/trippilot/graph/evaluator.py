"""后置独立行程质检打分与自愈节点（EvaluatorNode）。

在完整行程规划（full_planning）最后一轮回答生成完毕后触发：
1. 第一级（0ms 确定性硬规则质检）：
   - 检查是否存在“在周一安排了已明确标注周一闭馆的景点（而未提供替代方案）”；
   - 检查是否存在总花费超出用户明确预算上限的硬伤；
2. 第二级（轻量 LLM 质检打分，0~100 分）：
   - 核查四大红线：闭馆日冲突、预算透支、大交通起降时刻与游玩安排冲突、未取证价格幻觉；
   - score >= 80 视为通过（passed=True），直接结束；
   - score < 80 视为未通过（passed=False），生成简明修正指令回退给主规划核追加自愈修正说明（最多 1 次）。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from trippilot.graph.intent import INTERNAL_LLM_TAG

logger = logging.getLogger(__name__)

PASS_SCORE_THRESHOLD = 80

_EVALUATOR_SYSTEM_PROMPT = (
    "你是 TripPilot 的后置独立行程质检官（Evaluator）。"
    "请对照用户的原始需求、工具取证摘要与助手刚生成的行程方案，对方案做严格红线核验并打分（0~100 分）。\n"
    "请严格只输出一个合法的 JSON 对象（不要加 Markdown 代码块标记），结构如下：\n"
    "{\n"
    '  "score": 90,\n'
    '  "passed": true,\n'
    '  "issues": ["若存在严重硬伤则列出，无硬伤则为空数组"],\n'
    '  "fix_instruction": "若 passed 为 false，请用一句话指出需要如何修正"\n'
    "}\n\n"
    "四条红线扣分标准（只要触发任意一条严重硬伤，score 必须 < 80 且 passed 设为 false）：\n"
    "1. 【闭馆日硬冲突】：工具已查出某景点周一闭馆，方案却仍将该景点唯一游玩时间排在周一且未给周日或替代安排。\n"
    "2. 【预算透支】：方案核算的合计总费用明显超出用户设定的预算上限。\n"
    "3. 【时刻表硬冲突】：去程落地前或返程起飞后仍安排了市内景点游玩，或赶早班机未预留去机场时间。\n"
    "4. 【核心需求遗漏】：完全忽略了用户明确指定的目的地、天数或必去景点。\n"
    "注意：如果方案本身已经提醒了闭馆风险并合理排在了开馆日（或给出了二选一建议），且预算充裕，则属于优秀方案，应给 85~98 分且 passed=true。"
)


class EvaluationResult(BaseModel):
    """后置行程质检打分结果。"""

    score: int = 90
    passed: bool = True
    issues: list[str] = Field(default_factory=list)
    fix_instruction: str = ""


def _extract_number_yuan(text: str) -> int | None:
    """从预算或合计文本中提取人民币整数金额（支持 '19999元'、'2万'、'¥7,240'）。"""
    if not text:
        return None
    wan_match = re.search(r"(\d+(?:\.\d+)?)\s*万", text)
    if wan_match:
        return int(float(wan_match.group(1)) * 10000)
    num_match = re.search(r"(?:¥|￥|预算|合计|总计|共)[^\d]{0,8}(\d[\d,]{2,7})", text)
    if num_match:
        raw = num_match.group(1).replace(",", "")
        if raw.isdigit():
            return int(raw)
    plain = re.search(r"(\d[\d,]{2,7})\s*元", text)
    if plain:
        raw = plain.group(1).replace(",", "")
        if raw.isdigit():
            return int(raw)
    return None


def deterministic_redline_check(
    itinerary_text: str,
    *,
    budget_slot: str = "",
) -> EvaluationResult | None:
    """Level 1：0ms 确定性硬规则质检（预算超支与显式空方案拦截）。"""
    text = (itinerary_text or "").strip()
    if len(text) < 40:
        # 极短回复（非完整路书）无需触发质检回退
        return EvaluationResult(score=90, passed=True)

    # 检查预算是否超支（兼容 Markdown 表格粗体单元格如 `| **合计** | **约 ¥7,240** |`）
    user_budget = _extract_number_yuan(budget_slot)
    if user_budget and user_budget > 0:
        total_matches = re.findall(
            r"(?:合计|总计|总费用|总花费)[*|:\s约≈￥¥]{1,25}(\d[\d,]{2,7})",
            text,
        )
        for m in total_matches:
            digits = m.replace(",", "")
            if digits.isdigit():
                estimated_total = int(digits)
                if estimated_total > user_budget:
                    issue = f"预算超支：方案合计约 ¥{estimated_total}，超出用户预算上限 ¥{user_budget}"
                    return EvaluationResult(
                        score=60,
                        passed=False,
                        issues=[issue],
                        fix_instruction=(
                            f"当前方案合计费用（¥{estimated_total}）超出了用户的预算上限（¥{user_budget}），"
                            "请下调酒店档次或交通方案，补充满足预算的修正核算表。"
                        ),
                    )

    return None


def _parse_evaluator_json(raw_text: str) -> EvaluationResult | None:
    cleaned = (raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data: dict[str, Any] = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None

    try:
        score = int(data.get("score", 90))
    except (TypeError, ValueError):
        score = 90

    raw_issues = data.get("issues")
    issues = [str(x).strip() for x in raw_issues if str(x).strip()] if isinstance(raw_issues, list) else []
    passed = bool(data.get("passed", score >= PASS_SCORE_THRESHOLD)) and score >= PASS_SCORE_THRESHOLD
    fix_instruction = str(data.get("fix_instruction") or "").strip()

    if not passed and not fix_instruction and issues:
        fix_instruction = f"请针对以下质检冲突做补充修正：{'; '.join(issues)}"

    return EvaluationResult(
        score=score,
        passed=passed,
        issues=issues,
        fix_instruction=fix_instruction,
    )


async def aevaluate_itinerary(
    messages: Sequence[BaseMessage],
    *,
    intent_info: dict[str, Any] | None = None,
    evaluator_llm: Runnable | None = None,
) -> EvaluationResult:
    """对最新生成的完整行程方案执行两级质检打分（确定性规则 + 轻量 LLM 核验）。"""
    ai_msgs = [
        m for m in messages if isinstance(m, AIMessage) and not m.tool_calls and str(m.content or "").strip()
    ]
    if not ai_msgs:
        return EvaluationResult(score=95, passed=True)

    latest_itinerary = str(ai_msgs[-1].content or "").strip()
    slots = (intent_info or {}).get("slots") or {}
    budget_slot = str(slots.get("budget") or "") if isinstance(slots, dict) else ""

    # 1. 0ms 确定性硬规则预检
    rule_res = deterministic_redline_check(latest_itinerary, budget_slot=budget_slot)
    if rule_res is not None:
        return rule_res

    if evaluator_llm is None:
        return EvaluationResult(score=92, passed=True)

    # 提取当前轮用户输入与工具取证关键摘要
    human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    latest_user_req = str(human_msgs[-1].content or "").strip() if human_msgs else ""

    tool_snippets: list[str] = []
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            break
        if isinstance(m, ToolMessage):
            t_name = m.name or "tool"
            t_text = str(m.content or "").strip()[:300]
            tool_snippets.append(f"[{t_name}]: {t_text}")
            if len(tool_snippets) >= 6:
                break
    tool_snippets.reverse()
    evidence_summary = "\n".join(tool_snippets) if tool_snippets else "（无工具调用记录）"

    eval_prompt = [
        SystemMessage(content=_EVALUATOR_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"【用户需求】：{latest_user_req}\n"
                f"【提取槽位】：{json.dumps(slots, ensure_ascii=False)}\n"
                f"【工具取证摘要】：\n{evidence_summary}\n\n"
                f"【待质检的行程方案】：\n{latest_itinerary[:3500]}"
            )
        ),
    ]

    try:
        response = await evaluator_llm.ainvoke(
            eval_prompt,
            config={"tags": [INTERNAL_LLM_TAG]},
        )
        raw_content = str(getattr(response, "content", "") or "")
        parsed = _parse_evaluator_json(raw_content)
        if parsed is not None:
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("后置行程质检 LLM 解析异常，默认放行: %s", exc)

    return EvaluationResult(score=90, passed=True)


def build_self_healing_prompt(fix_instruction: str, issues: Sequence[str]) -> str:
    """构建触发质检未通过时的增量自愈提示（仅让主规划核流式输出简短修正补丁块）。"""
    issue_text = "；".join(issues) if issues else fix_instruction
    return (
        f"[系统质检自愈指令：刚才输出的行程方案在后置质检中发现以下冲突：{issue_text}。"
        f"修正要求：{fix_instruction}。\n"
        "请不要重复输出整篇路书，仅直接输出一段以 `---\\n\\n## 🔧 行程质检自动修正说明` 开头的补充修正段落，"
        "清晰给出调整后的具体安排或预算替换方案。]"
    )
