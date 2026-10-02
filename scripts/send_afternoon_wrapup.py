"""
매일 오후 4시, 하루 마무리 체크 이메일.
오늘 기록을 아직 안 남겼으면 재촉하고, 비어있는 정보가 있으면
"다른 팀도 이 정보를 기다리고 있다"는 걸 짚어서 공유 부족을 줄인다.

필요한 환경변수: send_morning_checkin.py와 동일
"""
import os
from datetime import datetime, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
RESEND_FROM = os.environ.get("RESEND_FROM", "브랜드슬램 업무보고 <onboarding@resend.dev>")
APP_URL = os.environ.get("DAILY_REPORT_APP_URL", "").strip()
ADMIN_BCC_EMAIL = os.environ.get("ADMIN_BCC_EMAIL", "").strip()  # 비워두면 BCC 안 걸림, 값 넣으면 그 주소로 사본 전송
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

SUPA = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def call_claude_compose(person, context):
    """고정 템플릿 대신, 그 사람의 하루를 보고 '전담 비서' 톤으로 마무리 이메일을 직접 쓰게 한다."""
    if not ANTHROPIC_API_KEY:
        return None
    system = (
        f"너는 {person}님의 업무를 전담으로 지원하는 비서다. 오후 4시, 하루 마무리 이메일을 쓴다.\n\n"
        "톤: 따뜻하고 개인적이되 존댓말로 간결하게. AI 티 내지 말고 진짜 비서가 챙겨주는 느낌으로.\n\n"
        "포함할 것 (해당되는 것만, 없으면 억지로 만들지 마라):\n"
        "1) 오늘 기록을 남겼는지에 대한 짧은 코멘트 (안 남겼으면 부드럽게 재촉, 남겼으면 짧게 인정)\n"
        "2) 구글드라이브 등에서 발견해서 미리 준비해둔 내용이 있으면 구체적으로 언급하고 업데이트 제안\n"
        "3) 비어있는 정보가 있으면 '다른 팀도 기다리고 있다'는 뉘앙스로 짧게\n"
        "4) 대표님이 주신 할일 중 안 끝난 게 있으면 짧게\n"
        "5) 다 끝났으면 가볍게 격려하고 끝\n"
        "6) 마지막에 바로가기 링크로 자연스럽게 연결\n\n"
        "전체 분량은 짧게. 출력 형식: 첫 줄은 'SUBJECT: 제목', 그 다음 줄부터는 본문만. 다른 설명 붙이지 마라."
    )
    try:
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-5", "max_tokens": 800, "system": system,
                  "messages": [{"role": "user", "content": context}]},
            timeout=30,
        )
        if res.status_code >= 300:
            print(f"  ⚠️ Claude 작성 실패({person}): {res.status_code} {res.text[:200]}")
            return None
        text = "".join(b.get("text", "") for b in res.json().get("content", []) if b.get("type") == "text").strip()
        if not text.startswith("SUBJECT:"):
            return None
        subject_line, _, body = text.partition("\n")
        subject = subject_line.replace("SUBJECT:", "").strip()
        return subject, body.strip()
    except Exception as e:
        print(f"  ⚠️ Claude 작성 중 오류({person}): {e}")
        return None


def send_email(to_addr, subject, body_text, purpose):
    payload = {"from": RESEND_FROM, "to": [to_addr], "subject": subject, "text": body_text}
    if ADMIN_BCC_EMAIL:
        payload["bcc"] = [ADMIN_BCC_EMAIL]
    res = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=15,
    )
    ok = res.status_code < 300
    SUPA.table("email_log").insert({
        "purpose": purpose, "recipient": to_addr, "subject": subject, "body": body_text,
        "status": "sent" if ok else "failed",
        "error": None if ok else f"{res.status_code} {res.text}"[:500],
    }).execute()
    if not ok:
        print(f"  ⚠️ {to_addr} 발송 실패: {res.status_code} {res.text}")
    return ok


def main():
    org_rows = SUPA.table("okr_org").select("person,email,pending").execute().data
    link_line = f"\n👉 바로가기: {APP_URL}\n" if APP_URL else ""
    today_str = datetime.now(timezone.utc).date().isoformat()
    sent, skipped = [], []

    for org in org_rows:
        person, email = org["person"], (org.get("email") or "").strip()
        if org.get("pending") or not email:
            skipped.append(f"{person} (이메일 없음/공석)")
            continue

        logs_today = (
            SUPA.table("daily_activity_log").select("id")
            .eq("staff_name", person).gte("created_at", today_str).execute().data
        )
        my_tasks = SUPA.table("assigned_tasks").select("*").eq("person", person).neq("status", "완료").execute().data
        my_prompts = SUPA.table("data_completeness_prompts").select("*").eq("person", person).eq("status", "open").execute().data
        my_drafts = SUPA.table("ai_drafted_updates").select("*").eq("person", person).eq("status", "pending").execute().data

        lines = []
        if not logs_today:
            lines.append("□ 오늘 아직 업무보고에 기록이 없어요 — 한 줄이라도 남겨주세요!")
        else:
            lines.append("□ 오늘 기록 확인됨 👍 — 더 추가할 내용 있으면 남겨주세요")

        if my_drafts:
            lines.append(f"\n□ 구글드라이브에서 발견한 내용 {len(my_drafts)}건 확인")
            for d in my_drafts[:3]:
                lines.append(f"   - {d['draft_content']}")

        if my_prompts:
            lines.append(f"\n□ 비어있는 정보 {len(my_prompts)}건 — 다른 팀도 이 정보를 기다리고 있어요")
            for p in my_prompts[:5]:
                lines.append(f"   - {p['message']}")

        if my_tasks:
            lines.append(f"\n□ 대표님이 주신 할일 중 아직 안 끝난 것 {len(my_tasks)}건")
            for t in my_tasks[:5]:
                lines.append(f"   - {t['title']}")

        if not my_prompts and not my_tasks and not my_drafts and logs_today:
            lines.append("\n오늘 할 일 다 하신 것 같아요 — 수고하셨습니다! 🎉")

        fallback_body = (
            f"{person}님, 하루 마무리할 시간이에요 (오후 4시).\n\n"
            + "\n".join(lines) + "\n"
            f"{link_line}\n"
            "- 브랜드슬램 업무보고 시스템 (자동발송)"
        )
        fallback_subject = f"[하루 마무리] {person}님 오후 체크인"

        context = (
            f"오늘 업무보고 기록 남겼는지: {'남김' if logs_today else '아직 안 남김'}\n"
            f"구글드라이브 등에서 미리 준비해둔 내용: {[d['draft_content'] for d in my_drafts] or '없음'}\n"
            f"비어있는 정보: {[p['message'] for p in my_prompts] or '없음'}\n"
            f"대표님이 주신 할일(미완료): {[t['title'] for t in my_tasks] or '없음'}\n"
            f"바로가기 링크: {APP_URL or '(없음)'}"
        )
        composed = call_claude_compose(person, context)
        subject, body = composed if composed else (fallback_subject, fallback_body)

        send_email(email, subject, body, purpose="afternoon_wrapup")
        sent.append(person)

    print("발송 완료:", sent)
    print("건너뜀:", skipped)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════
# Railway Cron: 0 16 * * *   (매일 오후 4시)
# Start Command: python scripts/send_afternoon_wrapup.py
# ══════════════════════════════════════════════════════════
