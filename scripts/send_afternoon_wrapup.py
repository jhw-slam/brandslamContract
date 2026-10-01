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

SUPA = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def send_email(to_addr, subject, body_text, purpose):
    res = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={"from": RESEND_FROM, "to": [to_addr], "subject": subject, "text": body_text},
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

        lines = []
        if not logs_today:
            lines.append("□ 오늘 아직 업무보고에 기록이 없어요 — 한 줄이라도 남겨주세요!")
        else:
            lines.append("□ 오늘 기록 확인됨 👍 — 더 추가할 내용 있으면 남겨주세요")

        if my_prompts:
            lines.append(f"\n□ 비어있는 정보 {len(my_prompts)}건 — 다른 팀도 이 정보를 기다리고 있어요")
            for p in my_prompts[:5]:
                lines.append(f"   - {p['message']}")

        if my_tasks:
            lines.append(f"\n□ 대표님이 주신 할일 중 아직 안 끝난 것 {len(my_tasks)}건")
            for t in my_tasks[:5]:
                lines.append(f"   - {t['title']}")

        if not my_prompts and not my_tasks and logs_today:
            lines.append("\n오늘 할 일 다 하신 것 같아요 — 수고하셨습니다! 🎉")

        body = (
            f"{person}님, 하루 마무리할 시간이에요 (오후 4시).\n\n"
            + "\n".join(lines) + "\n"
            f"{link_line}\n"
            "- 브랜드슬램 업무보고 시스템 (자동발송)"
        )
        send_email(email, f"[하루 마무리] {person}님 오후 체크인", body, purpose="afternoon_wrapup")
        sent.append(person)

    print("발송 완료:", sent)
    print("건너뜀:", skipped)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════
# Railway Cron: 0 16 * * *   (매일 오후 4시)
# Start Command: python scripts/send_afternoon_wrapup.py
# ══════════════════════════════════════════════════════════
