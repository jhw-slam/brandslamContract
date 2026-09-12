import os
import re
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Header
from supabase import create_client, Client
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tiro-webhook")

app = FastAPI()

# ── 환경 변수 ────────────────────────────────────────────────
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")  # service_role 키 사용 권장 (RLS 우회, 서버 전용)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5")
# Tiro 등 외부에서 아무나 이 엔드포인트를 못 때리게 막는 최소한의 장치.
# Railway 환경변수에 TIRO_WEBHOOK_SECRET을 설정하고, Tiro 웹훅 설정의 URL을
# https://<주소>/webhook?token=<같은값> 형태로 등록하면 됨 (헤더 커스터마이징이 안 되는
# 서비스가 많아 쿼리파라미터 방식을 기본값으로 둠).
TIRO_WEBHOOK_SECRET = os.environ.get("TIRO_WEBHOOK_SECRET")

if not (SUPABASE_URL and SUPABASE_KEY and ANTHROPIC_API_KEY):
    logger.warning("SUPABASE_URL / SUPABASE_KEY / ANTHROPIC_API_KEY 중 누락된 값이 있습니다.")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
anthropic = Anthropic(api_key=ANTHROPIC_API_KEY)


def split_summary(claude_text: str):
    """
    Claude 응답(프롬프트 형식: 1. 요약 / 2. 주요 결정사항 / 3. Action Items)을
    summary와 action_items 컬럼으로 분리. 형식이 안 맞으면 전체를 summary에 넣음.
    """
    match = re.search(r"(?:^|\n)\s*3\.\s*Action Items?(.*)", claude_text, re.IGNORECASE | re.DOTALL)
    if match:
        action_items = match.group(1).strip()
        summary = claude_text[: match.start()].strip()
        return summary, action_items
    return claude_text.strip(), None


def process_transcript_and_save(title: str, raw_text: str, raw_payload: dict):
    """Claude API 호출 후 Supabase meetings 테이블 저장"""
    prompt = f"""다음 회의 녹음 텍스트를 보고 핵심 내용과 Action Item을 정리해줘.

[회의 내용]
{raw_text}

[출력 형식]
1. 회의 요약 (3~5줄 이내)
2. 주요 결정사항
3. Action Items (담당자 및 할 일)
"""
    try:
        response = anthropic.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        summary_result = response.content[0].text
        summary, action_items = split_summary(summary_result)

        data = {
            "title": title,
            "raw_transcript": raw_text,
            "summary": summary,
            "action_items": action_items,
            "meeting_date": datetime.now(timezone.utc).isoformat(),
            "source": "tiro",
            "raw_payload": raw_payload,
        }
        supabase.table("meetings").insert(data).execute()
        logger.info(f"Saved meeting: {title}")
    except Exception:
        logger.exception(f"Error processing meeting '{title}'")


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
    # 간단한 토큰 검증 (설정 안 했으면 통과 — 개발 단계 편의용, 운영에서는 꼭 설정 권장)
    if TIRO_WEBHOOK_SECRET:
        provided = token or x_webhook_token
        if provided != TIRO_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="invalid webhook token")

    payload = await request.json()

    title = payload.get("title", payload.get("note", {}).get("title")) or (
        f"애플워치 회의록 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    transcript = (
        payload.get("transcript")
        or payload.get("text")
        or payload.get("note", {}).get("content")
        or ""
    )

    if not transcript:
        transcript = str(payload)

    background_tasks.add_task(process_transcript_and_save, title, transcript, payload)

    return {"status": "success", "message": "Webhook received"}
