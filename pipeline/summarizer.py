"""리포트 JSON → Slack 메시지.

LLM(DeepSeek)은 이미 계산된 JSON의 표기 문자열만 옮겨 쓰며 문장을 만든다.
검증 실패 시 1회 재생성, 그래도 실패하거나 API 키가 없으면 템플릿 리포트를 쓴다.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from pipeline.validator import ValidationResult, validate

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """당신은 광고 운영팀이 아침에 Slack으로 받아 보는 일일 성과 요약을 쓰는 어시스턴트입니다.
입력은 이미 계산이 끝난 리포트 JSON입니다. 아래 규칙을 반드시 지키세요.

[숫자 규칙]
- 금액(원)·비율(%)·건수(건)는 JSON에 있는 표기 문자열을 글자 그대로 옮겨 쓰세요. 예: "4,820,000원", "+12.4%", "312건"
- 직접 계산하거나 반올림하거나 "482만원"처럼 단위를 바꾸지 마세요. JSON에 없는 숫자는 쓰지 마세요.
- 값이 null 이면 그 숫자는 언급하지 마세요.

[내용 규칙]
- alerts 의 모든 항목을 빠짐없이 "⚠️ 확인 필요"에 넣고, 캠페인 이름을 JSON 표기 그대로 쓰세요.
- 원인은 단정하지 말고 "확인 필요", "가능성" 정도로 표현하세요.
- data_issues 가 있으면 마지막에 "⚙️ 데이터 이슈" 줄로 알려 주세요.

[출력 형식] Slack 텍스트, 아래 순서를 고정. 머리말·맺음말·코드블록 없이 본문만.
① 첫 줄: 한 줄 총평 (전체 소진액, 전일 대비 변화, 전환, CPA를 포함)
② 빈 줄 후 "⚠️ 확인 필요" 줄, 그 아래 "• [심각도] 내용" 형식으로 경보마다 한 줄 (경보가 없으면 "• 이상 징후 없음")
③ 빈 줄 후 "매체별" 줄, 그 아래 "• 매체명: 요약" 형식으로 매체마다 한 줄 (3줄 이내)
"""


@dataclass
class Composed:
    body: str
    source: str            # llm | template
    attempts: int
    validation: list[dict]
    reason: str | None = None
    model: str | None = None


# ── 템플릿 리포트 (LLM 없이도 항상 만들 수 있는 결정적 리포트) ──────────
def template_body(r: dict) -> str:
    lines = []
    t = r.get("total")
    if t:
        head = f"총 소진 {t['spend']}"
        if t["spend_change"]:
            head += f" (전일 대비 {t['spend_change']})"
        head += f" · 전환 {t['conversions']}"
        if t["cpa"]:
            head += f" · CPA {t['cpa']}"
        lines.append(head)
    else:
        lines.append("전일 성과 데이터가 없습니다.")

    lines += ["", "⚠️ 확인 필요"]
    lines += [f"• [{a['severity']}] {a['description']}" for a in r["alerts"]] or ["• 이상 징후 없음"]

    if r["by_media"]:
        lines += ["", "매체별"]
        for m in r["by_media"]:
            s = f"• {m['media']}: 소진 {m['spend']} (비중 {m['spend_share']}"
            s += f", 전일 대비 {m['spend_change']})" if m["spend_change"] else ")"
            s += f" · 전환 {m['conversions']}"
            if m["cpa"]:
                s += f" · CPA {m['cpa']}"
            lines.append(s)

    if r["data_issues"]:
        lines += ["", "⚙️ 데이터 이슈: " + " / ".join(r["data_issues"])]
    return "\n".join(lines)


# ── LLM 호출 (DeepSeek, OpenAI 호환 API) ──────────────────────────
def _api_key_env(llm_cfg: dict) -> str:
    return llm_cfg.get("api_key_env", "DEEPSEEK_API_KEY")


def _call_llm(report: dict, llm_cfg: dict, feedback: str | None) -> str:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ[_api_key_env(llm_cfg)],
        base_url=llm_cfg.get("base_url", "https://api.deepseek.com"),
        timeout=llm_cfg.get("timeout_seconds", 60),
    )
    user = "리포트 JSON:\n```json\n" + json.dumps(report, ensure_ascii=False, indent=1) + "\n```"
    if feedback:
        user += (
            f"\n\n이전 요약이 검증에 실패했습니다 ({feedback}). "
            "JSON 표기 문자열을 글자 그대로 옮겨 쓰고, 모든 경보 캠페인을 포함해 다시 작성하세요."
        )
    thinking = "enabled" if llm_cfg.get("thinking", False) else "disabled"
    resp = client.chat.completions.create(
        model=llm_cfg["model"],
        max_tokens=llm_cfg.get("max_tokens", 2000),
        temperature=llm_cfg.get("temperature", 0.2),  # thinking 모드에서는 무시됨
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        extra_body={"thinking": {"type": thinking}},
    )
    choice = resp.choices[0]
    if choice.finish_reason == "length":
        raise RuntimeError("LLM 출력이 max_tokens 에서 잘렸습니다")
    if choice.finish_reason == "content_filter":
        raise RuntimeError("LLM 출력이 콘텐츠 필터에 걸렸습니다")
    return (choice.message.content or "").strip()


def compose(report: dict, cfg: dict, use_llm: bool = True) -> Composed:
    llm_cfg = cfg.get("llm", {})
    if not (use_llm and llm_cfg.get("enabled", True)):
        return Composed(template_body(report), "template", 0, [], "LLM 사용 안 함")
    if not os.getenv(_api_key_env(llm_cfg)):
        return Composed(template_body(report), "template", 0, [], f"{_api_key_env(llm_cfg)} 없음")

    history: list[dict] = []
    feedback = None
    max_attempts = llm_cfg.get("max_attempts", 2)
    for attempt in range(1, max_attempts + 1):
        try:
            text = _call_llm(report, llm_cfg, feedback)
        except Exception as e:
            log.warning("LLM 호출 실패 (%d회차): %s", attempt, e)
            history.append({"attempt": attempt, "error": f"{type(e).__name__}: {e}"})
            break  # API 장애는 재시도해도 같을 가능성이 높아 바로 템플릿으로
        v: ValidationResult = validate(text, report)
        history.append(
            {"attempt": attempt, "ok": v.ok, "checked": v.checked,
             "mismatched": v.mismatched, "missing_alerts": v.missing_alerts}
        )
        if v.ok:
            return Composed(text, "llm", attempt, history, model=llm_cfg["model"])
        log.warning("요약 검증 실패 (%d회차): %s", attempt, v.feedback())
        feedback = v.feedback()

    return Composed(template_body(report), "template", len(history), history, "LLM 요약 검증/호출 실패")


def render_message(report: dict, composed: Composed) -> str:
    header = f"📊 {report['report_date_label']} 광고 성과 요약"
    footer = (
        f"_{composed.model} 요약 · 숫자 검증 통과_" if composed.source == "llm"
        else f"_템플릿 리포트 ({composed.reason})_"
    )
    return f"{header}\n{composed.body}\n\n{footer}"
