"""
매일 아침 9시, 오늘 할 일을 빈칸 채우기처럼 짧게 체크해달라는 이메일.
복잡한 설명 없이: 오늘 기록 1줄 + 대표님이 주신 할일 + 비어있는 정보만 간단히.

필요한 환경변수:
  SUPABASE_URL, SUPABASE_SERVICE_KEY
  RESEND_API_KEY, RESEND_FROM
  DAILY_REPORT_APP_URL   - 직원 업무보고 앱(독립 배포) 주소
"""
import os
import requests
from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
RESEND_FROM = os.environ.get("RESEND_FROM", "브랜드슬램 업무보고 <onboarding@resend.dev>")
APP_URL = os.environ.get("DAILY_REPORT_APP_URL", "").strip()
ADMIN_BCC_EMAIL = os.environ.get("ADMIN_BCC_EMAIL", "").strip()  # 비워두면 BCC 안 걸림, 값 넣으면 그 주소로 사본 전송

SUPA = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


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
    sent, skipped = [], []

    for org in org_rows:
        person, email = org["person"], (org.get("email") or "").strip()
        if org.get("pending") or not email:
            skipped.append(f"{person} (이메일 없음/공석)")
            continue

        my_tasks = SUPA.table("assigned_tasks").select("*").eq("person", person).neq("status", "완료").execute().data
        my_prompts = SUPA.table("data_completeness_prompts").select("*").eq("person", person).eq("status", "open").execute().data

        lines = ["□ 오늘 할 일 '업무보고'에 한 줄 남기기"]
        if my_tasks:
            lines.append(f"\n□ 대표님이 주신 할일 {len(my_tasks)}건")
            for t in my_tasks[:5]:
                lines.append(f"   - {t['title']}")
        if my_prompts:
            lines.append(f"\n□ 비어있는 정보 {len(my_prompts)}건 채우기")
            for p in my_prompts[:5]:
                lines.append(f"   - {p['message']}")

        body = (
            f"{person}님, 좋은 아침이에요!\n\n"
            "오늘 체크해주세요:\n\n" + "\n".join(lines) + "\n"
            f"{link_line}\n"
            "- 브랜드슬램 업무보고 시스템 (자동발송)"
        )
        send_email(email, f"[오늘 할 일] {person}님 아침 체크인", body, purpose="morning_checkin")
        sent.append(person)

    print("발송 완료:", sent)
    print("건너뜀:", skipped)


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════
# Railway Cron: 0 9 * * *   (매일 아침 9시)
# Start Command: python scripts/send_morning_checkin.py
# ══════════════════════════════════════════════════════════
