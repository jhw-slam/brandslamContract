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
TIRO_API_KEY = os.environ.get("TIRO_API_KEY")  # platform.tiro.ooo/me/api-keys 에서 발급 (점 포함 전체 키)
TIRO_WEBHOOK_SECRET = os.environ.get("TIRO_WEBHOOK_SECRET")

TIRO_API_BASE = "https://api.tiro.ooo"

if not (SUPABASE_URL and SUPABASE_KEY and ANTHROPIC_API_KEY):
    logger.warning("SUPABASE_URL / SUPABASE_KEY / ANTHROPIC_API_KEY 중 누락된 값이 있습니다.")
if not TIRO_API_KEY:
    logger.warning("TIRO_API_KEY가 없습니다 — 웹훅만으로는 실제 전사/요약 본문을 받을 수 없어 API로 재조회해야 합니다.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)


# ── Tiro REST API 조회 ──────────────────────────────────────
# 중요: Tiro 웹훅은 "무슨 일이 일어났다"는 이벤트(메타데이터)만 보내고,
# 실제 전사(paragraphs)·요약(summaries) 본문은 웹훅 payload에 들어있지 않습니다.
# (Tiro 공식 문서: "Webhook events carry metadata only ... Retrieve large content
#  such as transcripts and scripts from the separate APIs.")
# 그래서 webhook을 "트리거"로만 쓰고, 실제 내용은 noteGuid로 API를 다시 호출해서 가져와야 합니다.
def fetch_tiro_note_content(note_guid: str):
    headers = {"Authorization": f"Bearer {TIRO_API_KEY}"}
    transcript_text = ""
    tiro_summary_text = ""

    with httpx.Client(timeout=15) as client:
        try:
            r = client.get(f"{TIRO_API_BASE}/v1/external/notes/{note_guid}/paragraphs", headers=headers)
            r.raise_for_status()
            paragraphs = r.json()
            # 응답 형태가 리스트인지 {items:[...]}인지는 실제 응답을 raw_payload로 확인 후 맞춰야 함.
            items = paragraphs if isinstance(paragraphs, list) else paragraphs.get("items", paragraphs.get("data", []))
            texts = []
            for p in items or []:
                if isinstance(p, dict):
                    texts.append(p.get("text") or p.get("content") or "")
                else:
                    texts.append(str(p))
            transcript_text = "\n".join(t for t in texts if t)
        except Exception:
            logger.exception(f"paragraphs 조회 실패 (note_guid={note_guid})")

        try:
            r = client.get(f"{TIRO_API_BASE}/v1/external/notes/{note_guid}/summaries", headers=headers)
            r.raise_for_status()
            summaries = r.json()
            items = summaries if isinstance(summaries, list) else summaries.get("items", summaries.get("data", []))
            if items:
                first = items[0]
                tiro_summary_text = first.get("text") or first.get("content") or str(first)
        except Exception:
            logger.exception(f"summaries 조회 실패 (note_guid={note_guid})")

    return transcript_text, tiro_summary_text


def split_summary(claude_text: str):
    match = re.search(r"(?:^|\n)\s*3\.\s*Action Items?(.*)", claude_text, re.IGNORECASE | re.DOTALL)
    if match:
        return claude_text[: match.start()].strip(), match.group(1).strip()
    return claude_text.strip(), None


def summarize_with_claude(raw_text: str):
    prompt = f"""다음 회의 녹음 텍스트를 보고 핵심 내용과 Action Item을 정리해줘.

[회의 내용]
{raw_text}

[출력 형식]
1. 회의 요약 (3~5줄 이내)
2. 주요 결정사항
3. Action Items (담당자 및 할 일)
"""
    response = anthropic.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def process_tiro_event(event: dict):
    """
    Tiro 웹훅 이벤트 처리.
    event 구조 (Tiro 공식 문서 기준):
      { "id", "type", "createdAt", "workspaceGuid",
        "data": { "resourceType", "resourceId", "resource": {...} } }
    """
    data = event.get("data", {}) or {}
    resource_type = data.get("resourceType")
    resource_id = data.get("resourceId")
    event_type = event.get("type", "")
    resource = data.get("resource") or {}

    # note.deleted, voicefilejob 진행상황 등은 무시 — 실제 콘텐츠가 없거나 지워진 이벤트
    if "deleted" in event_type.lower():
        logger.info(f"무시된 이벤트(삭제됨): type={event_type}, resourceId={resource_id}")
        return
    if resource_type not in ("Note", "NoteSummary") or not resource_id:
        logger.info(f"무시된 이벤트: type={event_type}, resourceType={resource_type}")
        return
    # 0초짜리 테스트/빈 녹음은 저장할 내용이 없으므로 건너뜀
    if isinstance(resource, dict) and resource.get("recordingDurationSeconds") == 0:
        logger.info(f"무시된 이벤트(녹음 0초): resourceId={resource_id}")
        return

    title = f"Tiro 회의록 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    if isinstance(resource, dict) and resource.get("title") and resource["title"] != "Untitled":
        title = resource["title"]

    raw_text, tiro_summary = "", ""
    if TIRO_API_KEY:
        raw_text, tiro_summary = fetch_tiro_note_content(resource_id)

    summary, action_items = "", None
    if raw_text:
        try:
            claude_text = summarize_with_claude(raw_text)
            summary, action_items = split_summary(claude_text)
        except Exception:
            logger.exception("Claude 요약 실패 — Tiro 자체 요약으로 대체")
            summary = tiro_summary or ""
    else:
        # 본문을 못 가져온 경우에도, 나중에 원인 분석이 가능하도록 이벤트 자체는 저장해둔다.
        summary = tiro_summary or "(전사 본문을 가져오지 못했습니다 — raw_payload로 원본 이벤트를 확인하세요)"

    try:
        supabase.table("meetings").insert(
            {
                "title": title,
                "raw_transcript": raw_text or str(event),
                "summary": summary,
                "action_items": action_items,
                "meeting_date": datetime.now(timezone.utc).isoformat(),
                "source": "tiro",
                "raw_payload": event,
            }
        ).execute()
        logger.info(f"Saved meeting: {title}")
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
