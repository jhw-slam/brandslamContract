"""
매주 목요일 오후, 아직 '마감 PT'를 제출하지 않은 담당자에게
이번 주 마감 리포트를 제출하라고 이메일로 알려주는 스크립트.

필요한 환경변수 (GitHub Actions Secrets로 등록):
  SUPABASE_URL              - Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY       - Supabase 서비스 키 (okr_org / okr_items 읽기용)
  GMAIL_SENDER               - 발송자 Gmail 주소 (예: kbeauty@slam-global.com)
  GMAIL_APP_PASSWORD         - 위 계정의 Gmail 앱 비밀번호 (2단계 인증 계정에서 생성)
  OKR_APP_URL                - (선택) 실제 배포된 OKR 페이지 URL. 있으면 이메일 본문에 링크로 넣음
"""
import os
import smtplib
import ssl
from email.mime.text import MIMEText

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GMAIL_SENDER = os.environ["GMAIL_SENDER"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
OKR_APP_URL = os.environ.get("OKR_APP_URL", "").strip()

SUPA = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def send_email(to_addr, subject, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = GMAIL_SENDER
    msg["To"] = to_addr
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(GMAIL_SENDER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_SENDER, [to_addr], msg.as_string())


def main():
    org_rows = SUPA.table("okr_org").select("*").execute().data
    items = SUPA.table("okr_items").select("*").eq("cadence", "weekly").execute().data

    by_person = {}
    for it in items:
        by_person.setdefault(it["person"], []).append(it)

    link_line = f"\n제출하러 가기: {OKR_APP_URL}\n" if OKR_APP_URL else ""
    sent, skipped = [], []

    for org in org_rows:
        person = org["person"]
        email = (org.get("email") or "").strip()
        if org.get("pending") or not email:
            skipped.append(f"{person} (이메일 없음/공석)")
            continue

        my_items = by_person.get(person, [])
        not_submitted = [it for it in my_items if not it.get("closing_submitted")]
        if not my_items:
            skipped.append(f"{person} (주간 KPI 없음)")
            continue
        if not not_submitted:
            skipped.append(f"{person} (이미 전부 제출함)")
            continue

        lines = "\n".join(f"  - {it['title']} (진행 {it['progress']}/{it['target_qty']} {it['unit']})" for it in not_submitted)
        body = (
            f"{person}님, 안녕하세요.\n\n"
            f"이번 주 마감 PT를 아직 제출하지 않은 항목이 있어요:\n\n{lines}\n\n"
            "OKR 목표관리 페이지의 '🗓 마감 PT' 탭에서 이번 주 리포트를 작성해 제출해주세요.\n"
            "제출 후 관리자 확인을 받아야 이번 주가 정식으로 마감됩니다."
            f"{link_line}\n"
            "- 브랜드슬램 OKR 시스템 (자동발송)"
        )
        send_email(email, "[브랜드슬램 OKR] 이번 주 마감 PT 제출 요청", body)
        sent.append(person)

    print("발송 완료:", sent)
    print("건너뜀:", skipped)


if __name__ == "__main__":
    main()
