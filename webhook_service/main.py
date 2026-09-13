import os
import re
import logging
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Header
from supabase import create_client, Client
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tiro-webhook")

app = FastAPI()

# ── 환경 변수 ────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")  # service_role 키 (RLS 우회, 서버 전용)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
TIRO_API_KEY = os.environ.get("TIRO_API_KEY")  # platform.tiro.ooo/me/api-keys 에서 발급 (점 포함 전체 키). 전사 원문(paragraphs)까지 가져오고 싶을 때만 필요.
TIRO_WEBHOOK_SECRET = os.environ.get("TIRO_WEBHOOK_SECRET")

TIRO_API_BASE = "https://api.tiro.ooo"

if not (SUPABASE_URL and SUPABASE_KEY and ANTHROPIC_API_KEY):
    logger.warning("SUPABASE_URL / SUPABASE_KEY / ANTHROPIC_API_KEY 중 누락된 값이 있습니다.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)


# ── (선택) Tiro REST API로 전사 원문 조회 ───────────────────────
# note_summary.generated 이벤트에는 Tiro가 만든 요약문이 이미 들어있어서 이 호출 없이도
# 동작한다. 원문 전사(발화 그대로)까지 DB에 남기고 싶을 때만 노트 guid로 재조회한다.
# 반드시 진짜 노트 guid(resource.noteGuid / resource.note.guid)를 넣어야 하며,
# NoteSummary 이벤트의 data.resourceId(요약 자체의 id)를 넣으면 404가 난다.
def fetch_tiro_paragraphs(note_guid: str) -> str:
    if not TIRO_API_KEY or not note_guid:
        return ""
    headers = {"Authorization": f"Bearer {TIRO_API_KEY}"}
    try:
        with httpx.Client(timeout=15) as client:
            r = client.get(f"{TIRO_API_BASE}/v1/external/notes/{note_guid}/paragraphs", headers=headers)
            r.raise_for_status()
            paragraphs = r.json()
            items = paragraphs if isinstance(paragraphs, list) else paragraphs.get("items", paragraphs.get("data", []))
            texts = []
            for p in items or []:
                if isinstance(p, dict):
                    texts.append(p.get("text") or p.get("content") or "")
                else:
                    texts.append(str(p))
            return "\n".join(t for t in texts if t)
    except Exception:
        logger.exception(f"paragraphs 조회 실패 (note_guid={note_guid}) — 실패해도 Tiro 요약은 그대로 저장됨")
        return ""


def summarize_with_claude(tiro_summary_md: str):
    """Tiro가 만든 요약(마크다운)을 우리 포맷(요약/결정사항/Action Items)으로 재정리."""
    prompt = f"""다음은 Tiro가 회의 녹음을 보고 만든 요약문이야. 이걸 보고 핵심 내용과 Action Item을 정리해줘.

[Tiro 요약]
{tiro_summary_md}

[출력 형식]
제목: (10자 내외 짧은 제목)
1. 회의 요약 (3~5줄 이내)
2. 주요 결정사항
3. Action Items (담당자 및 할 일 — 담당자를 알 수 없으면 "미정"이라고 표기)
"""
    response = anthropic.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def parse_claude_output(claude_text: str):
    title = None
    title_match = re.search(r"제목:\s*(.+)", claude_text)
    if title_match:
        title = title_match.group(1).strip()
        claude_text = claude_text[: title_match.start()] + claude_text[title_match.end():]

    action_match = re.search(r"(?:^|\n)\s*3\.\s*Action Items?(.*)", claude_text, re.IGNORECASE | re.DOTALL)
    if action_match:
        summary = claude_text[: action_match.start()].strip()
        action_items = action_match.group(1).strip()
    else:
        summary, action_items = claude_text.strip(), None
    return title, summary, action_items


def process_tiro_event(event: dict):
    """
    Tiro 웹훅 이벤트 처리.

    실제로 확인된 사실:
    - note.created / note.ended / note.recording.completed / note.participants.updated
      등은 메타데이터 이벤트일 뿐, 회의 내용이 없다. → 무시.
    - note_summary.generated 이벤트에만 실제 요약 콘텐츠가 들어있다:
        data.resource.content.content   → Tiro가 만든 요약 원문(마크다운)
        data.resource.noteGuid / data.resource.note.guid → 진짜 노트 ID
      (data.resourceId는 요약 자체의 id이지 노트 id가 아니므로 API 조회에 쓰면 안 됨)
    """
    data = event.get("data", {}) or {}
    event_type = event.get("type", "")
    resource = data.get("resource") or {}

    if event_type != "note_summary.generated":
        logger.info(f"무시된 이벤트(콘텐츠 없음): type={event_type}")
        return

    tiro_summary_md = ((resource.get("content") or {}).get("content") or "").strip()
    note_guid = resource.get("noteGuid") or (resource.get("note") or {}).get("guid")

    if not tiro_summary_md:
        logger.warning(f"note_summary.generated인데 content가 비어있음: note_guid={note_guid}")
        return

    # 전사 원문은 선택 사항 (TIRO_API_KEY 있을 때만 시도, 실패해도 무시하고 계속 진행)
    raw_transcript = fetch_tiro_paragraphs(note_guid)

    title = f"Tiro 회의록 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    summary, action_items = tiro_summary_md, None
    try:
        claude_text = summarize_with_claude(tiro_summary_md)
        parsed_title, parsed_summary, parsed_action_items = parse_claude_output(claude_text)
        if parsed_title:
            title = parsed_title
        summary, action_items = parsed_summary, parsed_action_items
    except Exception:
        logger.exception("Claude 재정리 실패 — Tiro 원본 요약을 그대로 저장")

    try:
        supabase.table("meetings").insert(
            {
                "title": title,
                "raw_transcript": raw_transcript or tiro_summary_md,
                "summary": summary,
                "action_items": action_items,
                "meeting_date": datetime.now(timezone.utc).isoformat(),
                "source": "tiro",
                "raw_payload": event,
            }
        ).execute()
        logger.info(f"Saved meeting: {title} (note_guid={note_guid})")
    except Exception:
        logger.exception(f"Supabase 저장 실패 (title={title})")


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Tiro-Claude-Supabase Connector Running"}


@app.post("/webhook")
async def tiro_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    token: str | None = None,
    x_webhook_token: str | None = Header(default=None),
):
    if TIRO_WEBHOOK_SECRET:
        if (token or x_webhook_token) != TIRO_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="invalid webhook token")

    payload = await request.json()
    background_tasks.add_task(process_tiro_event, payload)
    return {"status": "success", "message": "Webhook received"}
