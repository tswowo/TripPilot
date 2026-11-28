"""两级混合意图识别与最小必填槽位门禁（IntentRouter & Minimal Slot Gate）。

核心职责：
1. Level 1（0ms 规则快筛）：秒级拦截纯问候寒暄（chitchat）、零槽位空泛规划请求（clarify）与显式单点快查（quick_lookup）；
2. Level 2（轻量 LLM 结构化提取）：基于最近 3 轮纯文本对话提取意图与累积旅行槽位（出发地、目的地、日期/天数、人数、预算、偏好）；
3. 最小必填槽位门禁（Minimal Viable Slots）：
   - 单点查询（quick_lookup）与行程微调（refinement）绝不追问；
   - 完整多日规划（full_planning）仅当缺失硬性槽位（目的地 destination、出发地 origin、出行日期/天数 date_or_days）时才降级为 clarify 追问；
   - 软槽位（人数、预算、偏好）缺失时绝不拦截，按合理默认先行规划；
4. 尾部瞬态引导（Ephemeral Tail Hint）：将意图与槽位提示仅附加于当次推理的最后一条 HumanMessage 内存副本末尾，
   不修改开头的 SystemMessage 与 Tools 前缀，100% 保护 Prefix KV Cache。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

IntentType = Literal[
    "chitchat",
    "clarify",
    "quick_lookup",
    "refinement",
    "full_planning",
]

INTERNAL_LLM_TAG = "internal_llm"

_CHITCHAT_EXACT: frozenset[str] = frozenset(
    {
        "你好",
        "您好",
        "hi",
        "hello",
        "hey",
        "嗨",
        "在吗",
        "早上好",
        "下午好",
        "晚上好",
        "你是谁",
        "你叫什么",
        "你是什么模型",
        "你能做什么",
        "你有什么功能",
        "谢谢",
        "多谢",
        "感谢",
        "辛苦了",
        "再见",
        "拜拜",
        "好的谢谢",
    }
)

_VAGUE_PLANNING_EXACT: frozenset[str] = frozenset(
    {
        "帮我做个旅游攻略",
        "帮我做个旅行攻略",
        "我想出去旅游",
        "我想去旅游",
        "帮我规划一下行程",
        "帮我规划行程",
        "做个旅游计划",
        "做个旅行计划",
        "规划个旅游方案",
    }
)

_PLANNING_KEYWORDS = ("攻略", "行程", "规划", "玩几天", "日游", "三天", "两天", "四天", "五天", "周末去")
_SINGLE_LOOKUP_KEYWORDS = (
    "天气",
    "几度",
    "下雨",
    "怎么走",
    "坐地铁",
    "打车多久",
    "多远",
    "开门",
    "闭馆",
    "营业时间",
    "预约",
)
_REFINEMENT_KEYWORDS = (
    "换成",
    "改成",
    "换一家",
    "换个酒店",
    "换个景点",
    "不去",
    "调整一下",
    "把第一天",
    "把第二天",
    "把周六",
    "把周日",
    "升级酒店",
)

_SLOT_LABELS: dict[str, str] = {
    "destination": "**想去哪个城市（目的地）**",
    "origin": "**从哪个城市出发**",
    "date_or_days": "**计划哪天出发（或大概玩几天）**",
}

_INTENT_CLASSIFIER_SYSTEM_PROMPT = (
    "你是 TripPilot 旅行规划引擎的前置意图与槽位提取器。"
    "请根据最近几轮用户与助手的对话，判断用户最新一条消息的意图，并累积提取已确认的旅行槽位。\n"
    "请严格只输出一个合法的 JSON 对象（不要包含 Markdown 代码块标记），结构如下：\n"
    "{\n"
    '  "intent": "chitchat | clarify | quick_lookup | refinement | full_planning",\n'
    '  "slots": {\n'
    '    "origin": "出发城市（未知则填空字符串）",\n'
    '    "destination": "目的地城市（未知则填空字符串）",\n'
    '    "date_or_days": "出行日期或游玩天数（未知则填空字符串）",\n'
    '    "travelers": "人数与关系（未知则填空字符串）",\n'
    '    "budget": "预算金额（未知则填空字符串）",\n'
    '    "preferences": "景点/住宿/饮食偏好（未知则填空字符串）"\n'
    "  },\n"
    '  "missing_hard_slots": ["destination", "origin", "date_or_days 中缺失的项"],\n'
    '  "clarify_question": "仅当 intent 为 clarify 时，用温和简短的一句话追问缺失的硬性槽位"\n'
    "}\n\n"
    "判定准则：\n"
    "1. chitchat：纯日常寒暄、问候、感谢、询问助手身份或与出行/地理完全无关的闲聊。\n"
    "2. quick_lookup：单点局部查询（例如：只查某城市天气、只问某两个地点之间怎么走/多久、只查某个景点的营业时间或单查某地酒店/机票），不需要排多日完整路书。\n"
    "3. refinement：上下文中已经有过行程方案，用户要求局部修改（如换酒店档次、替换某个景点、算其中某段交通耗时）。\n"
    "4. full_planning：用户希望生成多日/完整旅行行程规划，且结合历史上下文已具备【目的地 destination】、【出发地 origin】、【出行日期或天数 date_or_days】这三个核心硬槽位。\n"
    "5. clarify：用户希望生成完整旅行行程规划，但结合历史对话仍缺失 destination、origin 或 date_or_days 中的任意一项。"
    "注意：人数（travelers）、预算（budget）、偏好（preferences）属于可选软槽位，缺失时绝不能设为 clarify！"
)


class TravelSlots(BaseModel):
    """从多轮对话中累积提取的旅行核心槽位。"""

    origin: str = ""
    destination: str = ""
    date_or_days: str = ""
    travelers: str = ""
    budget: str = ""
    preferences: str = ""

    def merge_with(self, prior: TravelSlots | dict[str, Any] | None) -> TravelSlots:
        """与上一轮已提取的历史槽位合并（当前轮新提取的非空值优先，未提及的继承历史值）。"""
        if prior is None:
            return self
        prior_obj = (
            TravelSlots.model_validate(prior)
            if isinstance(prior, dict)
            else prior
        )
        return TravelSlots(
            origin=self.origin.strip() or prior_obj.origin.strip(),
            destination=self.destination.strip() or prior_obj.destination.strip(),
            date_or_days=self.date_or_days.strip() or prior_obj.date_or_days.strip(),
            travelers=self.travelers.strip() or prior_obj.travelers.strip(),
            budget=self.budget.strip() or prior_obj.budget.strip(),
            preferences=self.preferences.strip() or prior_obj.preferences.strip(),
        )

    def format_summary(self) -> str:
        parts: list[str] = []
        if self.origin:
            parts.append(f"出发地={self.origin}")
        if self.destination:
            parts.append(f"目的地={self.destination}")
        if self.date_or_days:
            parts.append(f"时间/天数={self.date_or_days}")
        if self.travelers:
            parts.append(f"人群={self.travelers}")
        if self.budget:
            parts.append(f"预算={self.budget}")
        if self.preferences:
            parts.append(f"偏好={self.preferences}")
        return "，".join(parts)


class IntentClassification(BaseModel):
    """前置意图识别与槽位门禁判定结果。"""

    intent: IntentType = "full_planning"
    slots: TravelSlots = Field(default_factory=TravelSlots)
    missing_hard_slots: list[str] = Field(default_factory=list)
    clarify_question: str = ""


def _normalize_text(text: str) -> str:
    return re.sub(r"[\s!！?？。，,~～、]+", "", (text or "").strip().lower())


def build_default_clarify_question(
    missing_slots: Sequence[str],
    slots: TravelSlots,
) -> str:
    """根据缺失的硬性必填槽位生成温和、不查户口的追问话术。"""
    labels = [_SLOT_LABELS[k] for k in missing_slots if k in _SLOT_LABELS]
    if not labels:
        labels = [
            _SLOT_LABELS["destination"],
            _SLOT_LABELS["origin"],
            _SLOT_LABELS["date_or_days"],
        ]

    known_prefix = ""
    if slots.destination:
        known_prefix = f"收到，计划去 **{slots.destination}**！"

    if len(labels) == 1:
        return (
            f"{known_prefix}为了帮你查询准确的往返交通与酒店排期，"
            f"请问你{labels[0]}是哪里/什么时候呢？"
        )

    items_str = "、".join(labels)
    return (
        f"{known_prefix}为了帮你核实真实的机票/高铁报价、天气与酒店排期，"
        f"请先告诉我：{items_str}？（预算和偏好也可以顺带告诉我，没有我先按高性价比经典方案为你规划）"
    )


def enforce_minimal_slot_gate(result: IntentClassification) -> IntentClassification:
    """执行“最小必填槽位”门禁校准：
    - 绝不因为缺失软槽位（人数、预算、偏好）而拦截用户；
    - 当意图为完整规划（full_planning 或 clarify）时，严格核对 destination / origin / date_or_days 三项硬槽位。
    """
    if result.intent in ("chitchat", "quick_lookup", "refinement"):
        result.missing_hard_slots = []
        result.clarify_question = ""
        return result

    slots = result.slots
    actual_missing: list[str] = []
    if not slots.destination.strip():
        actual_missing.append("destination")
    if not slots.origin.strip():
        actual_missing.append("origin")
    if not slots.date_or_days.strip():
        actual_missing.append("date_or_days")

    result.missing_hard_slots = actual_missing
    if actual_missing:
        result.intent = "clarify"
        if not result.clarify_question.strip():
            result.clarify_question = build_default_clarify_question(actual_missing, slots)
    else:
        # 三个核心硬槽位全部齐全，即使分类模型误判为 clarify 也自动放行进入 full_planning
        result.intent = "full_planning"
        result.clarify_question = ""

    return result


def fast_rule_classify(
    messages: Sequence[BaseMessage],
    *,
    prior_intent: str = "",
    prior_slots: TravelSlots | None = None,
) -> IntentClassification | None:
    """Level 1：0ms 确定性规则快筛。命中时直接返回分类结果，免去 LLM 调用。"""
    human_msgs = [m for m in messages if isinstance(m, HumanMessage)]
    if not human_msgs:
        return None

    inherited_slots = prior_slots.model_copy() if prior_slots is not None else TravelSlots()
    latest_text = str(human_msgs[-1].content or "").strip()
    norm = _normalize_text(latest_text)
    if not norm:
        return IntentClassification(intent="chitchat", slots=inherited_slots)

    # 1. 纯寒暄/问候/致谢/身份询问（0ms 直出）
    if norm in _CHITCHAT_EXACT:
        return IntentClassification(intent="chitchat", slots=inherited_slots)

    # 若上一轮处于 clarify 待用户补充槽位状态，必须走 Level 2 槽位提取与合并，禁止被快筛误判为单点快查或微调
    if prior_intent == "clarify":
        return None

    has_prior_assistant = any(
        isinstance(m, AIMessage) and bool(str(m.content or "").strip()) and not m.tool_calls
        for m in messages[:-1]
    )

    # 2. 首轮极短无槽位空泛规划请求（0ms 秒级追问拦截）
    if not has_prior_assistant and norm in _VAGUE_PLANNING_EXACT:
        missing = ["destination", "origin", "date_or_days"]
        slots = TravelSlots()
        return IntentClassification(
            intent="clarify",
            slots=slots,
            missing_hard_slots=missing,
            clarify_question=build_default_clarify_question(missing, slots),
        )

    # 3. 已有历史方案时的明确局部微调指令（保留已继承的历史槽位）
    if (
        has_prior_assistant
        and len(latest_text) <= 45
        and any(kw in latest_text for kw in _REFINEMENT_KEYWORDS)
    ):
        return IntentClassification(intent="refinement", slots=inherited_slots)

    # 4. 显式单点快查（如“南京明天天气怎么样”、“禄口机场到夫子庙坐地铁怎么走”）
    if (
        len(latest_text) <= 36
        and any(kw in latest_text for kw in _SINGLE_LOOKUP_KEYWORDS)
        and not any(pkw in latest_text for pkw in _PLANNING_KEYWORDS)
    ):
        return IntentClassification(intent="quick_lookup", slots=inherited_slots)

    return None


def _build_dialogue_digest(messages: Sequence[BaseMessage]) -> str:
    """构建供意图与槽位识别器消费的全量活跃对话摘要：
    直接消费当前 State 中的全部未归档消息（加上已归档的 trippilot_context_summary），
    不做硬编码的“最近 N 轮”截断——历史长度生命周期统一由 ContextCompactionMiddleware 的锯齿形压缩与 RemoveMessage 接管。
    仅剔除庞大的原始 ToolMessage，并对超长 AIMessage 路书正文提取首尾关键摘要。
    """
    summary_lines: list[str] = []
    dialogue_lines: list[str] = []

    for msg in messages:
        if isinstance(msg, SystemMessage) and getattr(msg, "id", None) == "trippilot_context_summary":
            text = str(msg.content or "").strip()
            if text:
                summary_lines.append(f"【历史归档摘要】:\n{text}")
        elif isinstance(msg, HumanMessage):
            text = str(msg.content or "").strip()
            if text:
                dialogue_lines.append(f"用户: {text}")
        elif isinstance(msg, AIMessage) and not msg.tool_calls:
            text = str(msg.content or "").strip()
            if text:
                if len(text) > 300:
                    text = f"{text[:200]} ... {text[-80:]}"
                dialogue_lines.append(f"助手: {text}")

    return "\n".join([*summary_lines, *dialogue_lines])


def _parse_classifier_json(
    raw_text: str,
    *,
    prior_slots: TravelSlots | None = None,
) -> IntentClassification | None:
    """从分类器 LLM 输出文本中稳健解析 JSON，合并历史槽位并执行最小必填槽位门禁校验。"""
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

    raw_intent = str(data.get("intent") or "full_planning").strip()
    if raw_intent not in ("chitchat", "clarify", "quick_lookup", "refinement", "full_planning"):
        raw_intent = "full_planning"

    raw_slots = data.get("slots") if isinstance(data.get("slots"), dict) else {}
    slots = TravelSlots(
        origin=str(raw_slots.get("origin") or "").strip(),
        destination=str(raw_slots.get("destination") or "").strip(),
        date_or_days=str(raw_slots.get("date_or_days") or "").strip(),
        travelers=str(raw_slots.get("travelers") or "").strip(),
        budget=str(raw_slots.get("budget") or "").strip(),
        preferences=str(raw_slots.get("preferences") or "").strip(),
    ).merge_with(prior_slots)

    missing = [
        str(x).strip()
        for x in (data.get("missing_hard_slots") or [])
        if str(x).strip() in _SLOT_LABELS
    ]
    clarify_q = str(data.get("clarify_question") or "").strip()

    classified = IntentClassification(
        intent=raw_intent,  # type: ignore[arg-type]
        slots=slots,
        missing_hard_slots=missing,
        clarify_question=clarify_q,
    )
    return enforce_minimal_slot_gate(classified)


async def aclassify_intent(
    messages: Sequence[BaseMessage],
    classifier_llm: Runnable | None = None,
    *,
    prior_info: dict[str, Any] | None = None,
) -> IntentClassification:
    """执行两级混合意图与槽位识别（0ms 规则快筛 -> 轻量 LLM 结构化提取 + 历史槽位继承 -> 最小槽位门禁校准）。"""
    prior_intent = str((prior_info or {}).get("intent") or "").strip()
    raw_prior_slots = (prior_info or {}).get("slots")
    prior_slots = (
        TravelSlots.model_validate(raw_prior_slots)
        if isinstance(raw_prior_slots, dict)
        else TravelSlots()
    )

    fast_result = fast_rule_classify(
        messages,
        prior_intent=prior_intent,
        prior_slots=prior_slots,
    )
    if fast_result is not None:
        return fast_result

    if classifier_llm is None:
        return IntentClassification(intent="full_planning", slots=prior_slots)

    digest = _build_dialogue_digest(messages)
    if not digest:
        return IntentClassification(intent="chitchat", slots=prior_slots)

    prior_summary = prior_slots.format_summary()
    prior_context = f"\n（历史已确认槽位：{prior_summary}）" if prior_summary else ""

    prompt_messages = [
        SystemMessage(content=_INTENT_CLASSIFIER_SYSTEM_PROMPT),
        HumanMessage(content=f"请分析以下对话并输出 JSON{prior_context}：\n\n{digest}"),
    ]
    try:
        response = await classifier_llm.ainvoke(
            prompt_messages,
            config={"tags": [INTERNAL_LLM_TAG]},
        )
        raw_content = str(getattr(response, "content", "") or "")
        parsed = _parse_classifier_json(raw_content, prior_slots=prior_slots)
        if parsed is not None:
            return parsed
    except Exception as exc:  # noqa: BLE001
        logger.debug("轻量意图识别 LLM 调用异常，降级为 full_planning: %s", exc)

    return IntentClassification(intent="full_planning", slots=prior_slots)


def build_ephemeral_tail_hint(classification: IntentClassification) -> str:
    """根据意图与槽位生成尾部瞬态引导提示语（仅追加于最后一条 HumanMessage 内存副本）。"""
    slot_summary = classification.slots.format_summary()
    slot_part = f"；已提取槽位：{slot_summary}" if slot_summary else ""

    if classification.intent == "quick_lookup":
        return (
            f"[系统路由提示：当前为单点快速查询(quick_lookup){slot_part}。"
            "请仅调用必要的 1~2 个高层工具直接简洁回答，无需套用完整多日路书模板。]"
        )
    if classification.intent == "refinement":
        return (
            f"[系统路由提示：当前为行程局部微调(refinement){slot_part}。"
            "请复用上下文已确认的事实，仅针对用户要求修改的部分调用对应工具并输出更新方案。]"
        )
    if classification.intent == "full_planning":
        return (
            f"[系统路由提示：当前为完整行程规划(full_planning){slot_part}。"
            "请按因果顺序并行调用高层工具（查两地天气、往返交通与酒店报价、用 discover_places 一次拿齐景点评分与闭馆时间），"
            "注意核对周一闭馆与预算约束，输出结构清晰的可落地行程。]"
        )
    return ""


def inject_ephemeral_intent_hint(
    model_messages: Sequence[BaseMessage],
    classification: IntentClassification | dict[str, Any] | None,
) -> list[BaseMessage]:
    """将尾部瞬态引导注入 model_messages 中最后一条 HumanMessage 的内存副本。

    - 绝不修改第 0 条 SystemMessage 及任何历史消息，保证 Prefix KV Cache 100% 命中；
    - 绝不修改持久化 state['messages']。
    """
    msgs = list(model_messages)
    if not msgs or classification is None:
        return msgs

    if isinstance(classification, dict):
        try:
            cls_obj = IntentClassification.model_validate(classification)
        except Exception:  # noqa: BLE001
            return msgs
    else:
        cls_obj = classification

    hint = build_ephemeral_tail_hint(cls_obj)
    if not hint:
        return msgs

    for idx in range(len(msgs) - 1, -1, -1):
        target = msgs[idx]
        if isinstance(target, HumanMessage) and isinstance(target.content, str):
            if hint not in target.content:
                msgs[idx] = target.model_copy(
                    update={"content": f"{target.content}\n\n{hint}"}
                )
            break

    return msgs
