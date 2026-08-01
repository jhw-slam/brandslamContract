"""
매주 목요일 오후, 아직 '마감 PT'를 제출하지 않은 담당자에게
이번 주 마감 리포트를 제출하라고 이메일로 알려주는 스크립트.

이메일 발송은 Resend(https://resend.com)의 API를 사용합니다.
구글 2단계 인증 / 앱 비밀번호가 전혀 필요 없고, API 키 하나만 있으면 됩니다.

필요한 환경변수 (GitHub Actions Secrets로 등록):
  SUPABASE_URL              - Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY       - Supabase 서비스 키 (okr_org / okr_items 읽기용)
  RESEND_API_KEY             - resend.com 가입 후 발급받은 API 키
  RESEND_FROM                - 보내는 사람 표시 (예: "브랜드슬램 OKR <onboarding@resend.dev>")
                                자체 도메인(slam-global.com)을 resend에 인증하면
                                "브랜드슬램 OKR <okr@slam-global.com>" 처럼 쓸 수 있음
  OKR_APP_URL                - (선택) 실제 배포된 OKR 페이지 URL. 있으면 이메일 본문에 링크로 넣음

resend 가입 방법 (2분):
  1. https://resend.com 접속 → 구글 계정으로 가입
  2. 왼쪽 메뉴 API Keys → Create API Key → 나오는 키를 RESEND_API_KEY로 저장
  3. (선택) Domains 메뉴에서 slam-global.com 인증하면 실제 회사 도메인으로 발송 가능.
     인증 안 해도 일단 onboarding@resend.dev 로 바로 보낼 수 있음 (테스트/내부용으로 충분)
"""
import os
import requests

from supabase import create_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
RESEND_API_KEY = os.environ["RESEND_API_KEY"]
RESEND_FROM = os.environ.get("RESEND_FROM", "브랜드슬램 OKR <onboarding@resend.dev>")
OKR_APP_URL = os.environ.get("OKR_APP_URL", "").strip()

SUPA = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def send_email(to_addr, subject, body_text, purpose="weekly_closing_reminder"):
    res = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
        json={
            "from": RESEND_FROM,
            "to": [to_addr],
            "subject": subject,
            "text": body_text,
        },
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
