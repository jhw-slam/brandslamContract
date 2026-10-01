"""
업무보고현황판 데이터 완성도 체크
──────────────────────────────────
Railway Cron Job으로 30분마다 실행되는 걸 전제로 만든 스크립트입니다.
(Streamlit 앱 자체는 사람이 페이지를 열어야만 코드가 돌아가므로,
 "누가 안 열어도 알아서 도는" 부분은 반드시 이렇게 별도 스크립트로 분리해야 합니다.)

하는 일:
1. sales_accounts / influencer_placements / sales_campaigns 테이블을 훑어서
   "이게 비어있으면 현황판이 불완전하다" 싶은 항목을 찾는다.
2. 새로 발견된 문제면 data_completeness_prompts에 기록하고,
   담당자에게 Resend로 이메일을 보낸다 (너무 자주 보내지 않도록 6시간 간격 제한).
3. 이미 고쳐진 문제는 자동으로 resolved 처리한다.

Railway 설정 방법은 이 파일 맨 아래 주석 참고.
"""

import os
import json
from datetime import datetime, timedelta, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
EMAIL_RESEND_COOLDOWN_HOURS = 6

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다.")

SUPA = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_person_email(person):
    rows = SUPA.table("okr_org").select("email").eq("person", person).execute().data
    return rows[0]["email"] if rows and rows[0].get("email") else None


def send_email(to_email, subject, body_text):
    if not RESEND_API_KEY or not to_email:
        return False
    try:
        res = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={
                "from": "브랜드슬램 현황판 <noreply@brandslam-notify.com>",
                "to": [to_email],
                "subject": subject,
                "text": body_text,
            },
            timeout=15,
        )
        return res.status_code < 300
    except Exception as e:
        print(f"이메일 발송 실패: {e}")
        return False


def upsert_prompt(person, record_type, record_id, message):
    """같은 record_type+record_id로 이미 열려있는(open) 요청이 있으면 중복 생성 안 함."""
    existing = (
        SUPA.table("data_completeness_prompts")
        .select("*")
        .eq("record_type", record_type)
        .eq("record_id", record_id)
        .eq("status", "open")
        .execute()
        .data
    )
    if existing:
        return existing[0]  # 이미 열려있음 — 재사용
    res = SUPA.table("data_completeness_prompts").insert({
        "person": person, "record_type": record_type, "record_id": record_id, "message": message,
    }).execute()
    return res.data[0] if res.data else None


def maybe_email(prompt):
    last = prompt.get("last_emailed_at")
    if last:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - last_dt < timedelta(hours=EMAIL_RESEND_COOLDOWN_HOURS):
            return
    email = get_person_email(prompt["person"])
    if not email:
        return
    sent = send_email(
        email,
        "📋 업무현황판 — 채워주셔야 할 정보가 있어요",
        f"{prompt['message']}\n\n업무보고 페이지에서 바로 채워주시면 됩니다.",
    )
    if sent:
        SUPA.table("data_completeness_prompts").update({"last_emailed_at": now_iso()}).eq("id", prompt["id"]).execute()


def auto_resolve_fixed(record_type, record_id, still_broken):
    """더 이상 문제가 아니면(=still_broken이 False) 열려있던 요청을 자동으로 닫는다."""
    if still_broken:
        return
    SUPA.table("data_completeness_prompts").update({
        "status": "resolved", "resolved_at": now_iso(),
    }).eq("record_type", record_type).eq("record_id", record_id).eq("status", "open").execute()


def check_sales_accounts():
    accounts = SUPA.table("sales_accounts").select("*").neq("status", "종료").neq("status", "이탈").execute().data
    for a in accounts:
        missing_contact = not a.get("contact_email") and not a.get("contact_phone")
        auto_resolve_fixed("sales_account_contact", a["id"], missing_contact)
        if missing_contact:
            p = upsert_prompt(
                a["assigned_to"], "sales_account_contact", a["id"],
                f"[{a['brand_name']}] 담당자 연락처(이메일 또는 전화번호)가 비어있어요 — 계정 관리 탭에서 채워주세요.",
            )
            if p:
                maybe_email(p)

        missing_renewal = a["status"] == "운영중" and not a.get("renewal_date")
        auto_resolve_fixed("sales_account_renewal", a["id"], missing_renewal)
        if missing_renewal:
            p = upsert_prompt(
                a["assigned_to"], "sales_account_renewal", a["id"],
                f"[{a['brand_name']}] 운영중인데 다음 갱신/온보딩일이 비어있어요 — 계정 관리 탭에서 채워주세요.",
            )
            if p:
                maybe_email(p)


def check_influencer_placements():
    rows = (
        SUPA.table("influencer_placements")
        .select("id,brand_name,influencer_name,status,content_link,assigned_to")
        .eq("status", "업로드완료")
        .execute()
        .data
    )
    for r in rows:
        missing_link = not r.get("content_link")
        auto_resolve_fixed("placement_missing_link", r["id"], missing_link)
        if missing_link:
            p = upsert_prompt(
                r["assigned_to"], "placement_missing_link", r["id"],
                f"[{r['brand_name']}·{r['influencer_name']}] 업로드완료 상태인데 콘텐츠 링크가 비어있어요 — 업체별 진행 현황에서 채워주세요.",
            )
            if p:
                maybe_email(p)


def check_sales_campaigns():
    campaigns = SUPA.table("sales_campaigns").select("*").neq("status", "완료").execute().data
    today = datetime.now(timezone.utc).date()
    for c in campaigns:
        open_date = datetime.fromisoformat(c["open_date"]).date()
        overdue = (today - open_date).days > 35
        auto_resolve_fixed("campaign_overdue", c["id"], overdue)
        if overdue:
            p = upsert_prompt(
                c.get("created_by") or "미지정", "campaign_overdue", c["id"],
                f"캠페인 '{c['campaign_name']}'이 오픈된 지 35일 넘었는데 아직 '완료' 처리가 안 됐어요 — 캠페인·주차루틴에서 상태를 확인해주세요.",
            )
            if p:
                maybe_email(p)


WHITELISTED_FIELDS = {
    "sales_accounts": ["contact_name", "contact_email", "contact_phone", "monthly_budget",
                        "contract_start", "renewal_date", "status"],
    "dev_tasks": ["status", "description"],
    "influencer_pool": ["relationship_status", "last_collab_date", "rate"],
    "casting_funnel": ["stage", "test_rate"],
}


def call_claude_extract_fields(target_table, draft_content, correction_note):
    """자유 텍스트(수정사항)를 보고, 화이트리스트에 있는 필드만 골라서 업데이트값을 뽑아낸다.
    확신 없는 필드는 아예 포함하지 않는다 — 틀린 자동수정보다 '그냥 notes에만 남기는 것'이 안전하다."""
    if not ANTHROPIC_API_KEY:
        return {}
    allowed = WHITELISTED_FIELDS.get(target_table, [])
    if not allowed:
        return {}
    system = (
        f"너는 '{target_table}' 테이블의 레코드를 고치는 보조원이다. 아래 자유 텍스트(직원이 쓴 수정사항)를 보고, "
        f"이 필드들 중에서만({', '.join(allowed)}) 확실하게 바뀌어야 하는 값을 뽑아내라. "
        "확신 없는 필드는 절대 포함하지 마라 — 틀리게 채우는 것보다 비워두는 게 낫다. "
        "날짜는 YYYY-MM-DD 형식으로. 출력은 오직 JSON 객체만: {\"필드명\": \"값\", ...}. 해당하는 게 없으면 {}."
    )
    user_content = f"원래 AI가 찾은 내용: {draft_content}\n\n직원이 남긴 수정사항: {correction_note}"
    try:
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-sonnet-5", "max_tokens": 500, "system": system,
                  "messages": [{"role": "user", "content": user_content}]},
            timeout=30,
        )
        if res.status_code >= 300:
            return {}
        text = "".join(b.get("text", "") for b in res.json().get("content", []) if b.get("type") == "text").strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        data = json.loads(text)
        return {k: v for k, v in data.items() if k in allowed}
    except Exception as e:
        print(f"필드 추출 실패: {e}")
        return {}


def process_correction_requests():
    """직원이 '수정사항'란에 자유롭게 쓴 글을 읽고, 알아서 정리해서 실제 레코드에 반영한다."""
    requests_open = (
        SUPA.table("ai_drafted_updates").select("*").eq("status", "correction_requested").execute().data
    )
    for req in requests_open:
        target_table = req["target_table"]
        record_id = req.get("target_record_id")
        note = req.get("correction_note") or ""

        fields = call_claude_extract_fields(target_table, req["draft_content"], note)

        if record_id:
            update_payload = dict(fields)
            # 화이트리스트 필드 업데이트와 별개로, 원문은 항상 notes에 그대로 남겨서 추적 가능하게 함
            existing = SUPA.table(target_table).select("notes").eq("id", record_id).execute().data
            old_notes = (existing[0].get("notes") or "") if existing else ""
            update_payload["notes"] = (old_notes + f"\n[{now_iso()[:10]} 자동반영] {note}").strip()
            SUPA.table(target_table).update(update_payload).eq("id", record_id).execute()
        else:
            print(f"  ⚠️ {req['person']}의 수정요청에 연결된 레코드가 없어서, 기록만 남기고 넘어감: {note[:50]}")

        SUPA.table("ai_drafted_updates").update({
            "status": "applied", "resolved_at": now_iso(),
        }).eq("id", req["id"]).execute()
        print(f"  ✅ {req['person']}의 수정사항 반영 완료 ({len(fields)}개 필드)")


def main():
    check_sales_accounts()
    check_influencer_placements()
    check_sales_campaigns()
    process_correction_requests()
    print(f"[{now_iso()}] 완성도 체크 완료")


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════
# Railway Cron 설정 방법
# ══════════════════════════════════════════════════════════
# 1. 이 파일을 메인 레포(brandslamContract)의 scripts/completeness_check.py 로 추가
# 2. Railway 대시보드 → 같은 프로젝트 안에 "New Service" → "Empty Service" 추가
# 3. 그 서비스 Settings에서:
#    - Source: 같은 GitHub 레포 연결
#    - Cron Schedule: */30 * * * *  (30분마다)
#    - Start Command: python scripts/completeness_check.py
#    - Variables: SUPABASE_URL, SUPABASE_SERVICE_KEY, RESEND_API_KEY
#      (메인 서비스 Variables에서 그대로 복사해서 붙여넣기)
# 4. requirements.txt에 requests, supabase 패키지가 있는지 확인 (이미 메인 레포에 있음)
