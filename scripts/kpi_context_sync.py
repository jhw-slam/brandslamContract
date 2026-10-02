"""
KPI ↔ DB 맥락 정렬 스크립트
──────────────────────────
Railway Cron으로 하루 2번(예: 09:00, 18:00) 실행하는 걸 전제로 만들었습니다.

하는 일 (사람별로):
1. 이 사람의 현재 Objective/KR/KPI(okr_org, okr_items)를 가져온다.
2. 이 사람 역할에 맞는 실제 업무 데이터(sales_accounts, dev_tasks, influencer_pool,
   casting_funnel, daily_activity_log 등)를 가져온다.
3. Claude에게 "이 사람의 목표를 지금 데이터 구조로 잘 추적하고 있는지" 분석을 맡긴다.
4. Claude의 제안을 kpi_alignment_suggestions 테이블에 저장한다 (사람이 검토 후 반영).

** 절대 하지 않는 것: 이 스크립트는 ALTER TABLE 등 스키마 변경을 직접 실행하지 않습니다. **
** 분석/제안까지만 자동화하고, 실제 테이블 구조 변경은 항상 사람 검토를 거칩니다.       **
"""

import os
import io
import json
from datetime import datetime, timezone

import requests
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")  # 서비스 계정 키 전체 내용(JSON 문자열)
DRIVE_ROOT_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")  # 이미 있는 환경변수 재사용

if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다.")
if not ANTHROPIC_API_KEY:
    raise SystemExit("ANTHROPIC_API_KEY 환경변수가 없습니다.")

SUPA = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def list_drive_files(max_files=200):
    """구글드라이브 특정 폴더(DRIVE_ROOT_FOLDER_ID) 아래 파일을 전부 나열한다.
    서비스 계정에 그 폴더가 '뷰어'로 공유되어 있어야 동작한다.
    설정 안 돼있으면 조용히 빈 리스트를 반환 (이 기능 없이도 나머지는 정상 동작)."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not DRIVE_ROOT_FOLDER_ID:
        return []
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = build("drive", "v3", credentials=creds)

        files, page_token, folders_to_scan = [], None, [DRIVE_ROOT_FOLDER_ID]
        scanned = set()
        while folders_to_scan and len(files) < max_files:
            fid = folders_to_scan.pop(0)
            if fid in scanned:
                continue
            scanned.add(fid)
            page_token = None
            while True:
                res = service.files().list(
                    q=f"'{fid}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime)",
                    pageToken=page_token, pageSize=100,
                    supportsAllDrives=True, includeItemsFromAllDrives=True,
                    corpora="allDrives",
                ).execute()
                for f in res.get("files", []):
                    if f["mimeType"] == "application/vnd.google-apps.folder":
                        folders_to_scan.append(f["id"])
                    else:
                        files.append(f)
                page_token = res.get("nextPageToken")
                if not page_token:
                    break
        return files[:max_files]
    except Exception as e:
        print(f"구글드라이브 조회 실패(설정 확인 필요): {e}")
        return []

STAFF_NAMES = ["김선재", "곽재선", "구정회", "이단우"]
ROLE_MAP = {"김선재": "sales", "곽재선": "influencer", "구정회": "dev", "이단우": "china_ops"}


def gather_role_data(person, role):
    """역할별로 흩어진 실제 업무 데이터를 모아온다."""
    data = {}
    if role == "sales":
        data["sales_accounts"] = SUPA.table("sales_accounts").select("*").eq("assigned_to", person).execute().data
        data["sales_issues_open"] = (
            SUPA.table("sales_issues").select("*, sales_accounts!inner(assigned_to)")
            .eq("sales_accounts.assigned_to", person).neq("status", "해결됨").execute().data
        )
        data["sales_campaigns"] = (
            SUPA.table("sales_campaigns").select("*, sales_accounts!inner(assigned_to)")
            .eq("sales_accounts.assigned_to", person).execute().data
        )
    elif role == "dev":
        data["dev_tasks"] = SUPA.table("dev_tasks").select("*").eq("person", person).execute().data
    elif role == "influencer":
        data["influencer_pool"] = SUPA.table("influencer_pool").select("*").eq("assigned_to", person).execute().data
    elif role == "china_ops":
        data["casting_funnel"] = SUPA.table("casting_funnel").select("*").eq("assigned_to", person).execute().data

    data["influencer_placements"] = (
        SUPA.table("influencer_placements").select("*").eq("assigned_to", person)
        .order("created_at", desc=True).limit(20).execute().data
    )
    data["recent_logs"] = (
        SUPA.table("daily_activity_log").select("note,created_at").eq("staff_name", person)
        .order("created_at", desc=True).limit(10).execute().data
    )
    data["open_completeness_prompts"] = (
        SUPA.table("data_completeness_prompts").select("message").eq("person", person).eq("status", "open").execute().data
    )
    data["already_registered_data_sources"] = (
        SUPA.table("kpi_data_sources").select("related_suggestion_text,source_url").eq("person", person).execute().data
    )
    return data


def _existing_record_names(role, role_data):
    """역할별로 '이름으로 구글드라이브 파일과 매칭해볼 만한' 기존 레코드 목록을 뽑는다.
    - 세일즈(김선재): 브랜드 계정명 (sales_accounts)
    - 인플루언서(곽재선)/중국운영(이단우): 담당 캠페인의 '브랜드명' — 리스팅·업로드 관리 파일엔
      인플루언서 개인 이름보다 브랜드명이 적혀있는 경우가 많아서 influencer_placements 기준으로 잡는다.
    - 개발(구정회): 아직 명확한 매칭 기준이 없어 dev_tasks 제목으로 임시 설정 — 추후 재정의 필요."""
    if role == "sales":
        return [a.get("brand_name") for a in role_data.get("sales_accounts", []) if a.get("brand_name")]
    if role in ("influencer", "china_ops"):
        brands = {p.get("brand_name") for p in role_data.get("influencer_placements", []) if p.get("brand_name")}
        return list(brands)
    if role == "dev":
        return [t.get("title") for t in role_data.get("dev_tasks", []) if t.get("title")]
    return []


def call_claude_analysis(person, okr_org_row, okr_items, role_data, drive_files, role):
    system = (
        "너는 이 회사의 데이터 아키텍트 겸 PM이다. 한 사람의 OKR/KPI(목표)와, 그걸 추적하기 위해 실제로 "
        "쌓이고 있는 업무 데이터, 그리고 구글드라이브에 있는 관련 파일 목록까지 비교해서 분석하는 게 네 역할이다.\n\n"
        "확인할 것 (세 종류의 제안을 만들 수 있다):\n"
        "1) kpi_gap — 지금 목표(Objective/KR)를 현재 데이터 구조로 제대로 추적할 수 있는가? 필요한 정보인데 "
        "어디에도 기록이 안 되거나 여러 곳에 흩어져서 한눈에 안 보이면 이 유형으로 제안해라. 데이터가 부족해서 "
        "못 보던 거라면 '구글시트 연동해드릴까요?' 식으로 쉬운 대안도 같이 제안해라. "
        "⚠️ already_registered_data_sources에 이미 비슷한 내용으로 소스가 등록되어 있으면, 똑같은 제안을 "
        "또 만들지 마라 — 이미 해결된 것으로 보고 건너뛰어라.\n"
        "2) drive_link — 구글드라이브 파일 목록 중에, 이 사람의 업무 데이터(existing_record_names에 있는 "
        "항목들 — 역할에 따라 브랜드/작업/인플루언서/캐스팅 대상 등 다양하다)와 이름이 겹치거나 관련 있어 "
        "보이는 파일이 있으면 '이 파일을 연동할지 물어보자'는 제안을 해라. 파일명과 왜 관련있어 보이는지, "
        "그리고 **이걸 실제로 반영하려면 아직 뭐가 더 필요한지(보완 요소)**도 반드시 같이 적어라 "
        "(예: '날짜/금액은 파일명만으론 알 수 없어서 본인 확인 필요'). 두 가지 경우로 나뉜다:\n"
        "   (a) 파일명에서 짐작되는 이름이 existing_record_names에 없다 → 새 레코드가 필요한 경우다. "
        "suggest_new_account를 true로, suggested_brand_name에 짐작되는 이름을 적어라 (회사 전체에 영향을 "
        "주는 일이라 대표 승인을 거친다). 세일즈가 아닌 역할이면 이 경우는 잘 안 나올 것이다.\n"
        "   (b) 그 이름이 이미 existing_record_names에 있다 → 이미 등록된 자기 자신의 레코드를 보완하는 "
        "것뿐이다. is_personal_existing_match를 true로 해라 (본인이 바로 확인하면 되고 대표 승인은 필요 없다).\n"
        "3) org_improvement — 이 사람의 최근 업무기록(recent_logs) 내용을 보고, 일하는 방식이나 조직 구조에서 "
        "대표가 바꾸면 좋을 것 같은 게 보이면 제안해라 (예: 특정 업무에 시간이 과도하게 쏠림, 반복되는 병목 등). "
        "확실하지 않으면 이 유형은 만들지 마라 — 추측으로 조직 얘기를 하는 건 위험하다.\n\n"
        "주의사항:\n"
        "- 너는 테이블/컬럼이나 실제 파일 연동을 직접 실행할 수 없다. 제안만 해라 (실제 반영은 사람이 검토 후 한다).\n"
        "- 추측성 숫자나 확인 안 된 사실을 단정하지 마라.\n"
        "- 정말 문제가 없으면 빈 배열을 반환해라. 억지로 제안을 만들어내지 마라. 전체 최대 3개까지.\n\n"
        "출력은 오직 JSON 배열만: [{\"suggestion_type\": \"kpi_gap|drive_link|org_improvement\", "
        "\"suggestion\": \"짧고 구체적인 제안(80자 이내)\", "
        "\"drive_file_name\": \"drive_link일 때만, 아니면 null\", \"drive_file_url\": \"drive_link일 때만, 아니면 null\", "
        "\"suggest_new_account\": true 또는 false, \"suggested_brand_name\": \"신규계정 제안일 때만, 아니면 null\", "
        "\"is_personal_existing_match\": true 또는 false}]. "
        "다른 텍스트는 절대 포함하지 마라."
    )
    user_content = json.dumps({
        "person": person,
        "objective": okr_org_row.get("objective") if okr_org_row else None,
        "krs": okr_org_row.get("krs") if okr_org_row else [],
        "okr_items": [{"category": i.get("category"), "title": i["title"], "progress": i.get("progress"),
                       "target": i.get("target_qty"), "is_recurring": i.get("is_recurring")} for i in okr_items],
        "role_data_summary": {k: len(v) if isinstance(v, list) else v for k, v in role_data.items()},
        "role_data_sample": {k: v[:5] if isinstance(v, list) else v for k, v in role_data.items()},
        "existing_record_names": _existing_record_names(role, role_data),
        "recent_work_logs": [l.get("note") for l in role_data.get("recent_logs", [])],
        "drive_files": [{"name": f["name"], "url": f.get("webViewLink")} for f in drive_files[:50]],
        "already_registered_data_sources": role_data.get("already_registered_data_sources", []),
    }, ensure_ascii=False, default=str)

    res = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": "claude-sonnet-5", "max_tokens": 1500, "system": system,
              "messages": [{"role": "user", "content": user_content}]},
        timeout=60,
    )
    if res.status_code >= 300:
        print(f"{person}: API 오류 {res.status_code} {res.text[:200]}")
        return []
    text = "".join(b.get("text", "") for b in res.json().get("content", []) if b.get("type") == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print(f"{person}: JSON 파싱 실패 — {text[:200]}")
        return []


def check_sales_meetings(person, sales_accounts):
    """세일즈 담당자에게만: 본인이 등록한 '업체명'이 실제로 언급된 회의만 골라서 알림.
    다른 회의(재무/인사/기타)는 절대 안 보여준다 — 업체명 키워드 매칭이 기준."""
    brand_names = [a["brand_name"].strip() for a in sales_accounts if a.get("brand_name")]
    if not brand_names:
        return

    already_alerted = {
        a["meeting_id"] for a in
        SUPA.table("sales_meeting_alerts").select("meeting_id").eq("person", person).execute().data
    }

    recent_meetings = (
        SUPA.table("meetings").select("id,title,meeting_date,summary,raw_transcript")
        .order("meeting_date", desc=True).limit(100).execute().data
    )
    for m in recent_meetings:
        if m["id"] in already_alerted:
            continue
        haystack = f"{m.get('title') or ''} {m.get('summary') or ''} {m.get('raw_transcript') or ''}"
        matched = next((b for b in brand_names if b and b in haystack), None)
        if not matched:
            continue
        snippet = (m.get("summary") or "")[:200]
        SUPA.table("sales_meeting_alerts").insert({
            "person": person, "meeting_id": m["id"], "brand_matched": matched,
            "meeting_title": m.get("title"), "meeting_date": m.get("meeting_date"),
            "summary_snippet": snippet,
        }).execute()
        print(f"  [{person}] 회의 알림 생성: {m.get('title')} (업체명 '{matched}' 매칭)")


def main():
    drive_files = list_drive_files()
    print(f"구글드라이브 파일 {len(drive_files)}개 확인됨" if drive_files else "구글드라이브 연동 미설정 — 이 부분은 건너뜀")

    for person in STAFF_NAMES:
        role = ROLE_MAP.get(person)
        okr_org_rows = SUPA.table("okr_org").select("*").eq("person", person).execute().data
        okr_org_row = okr_org_rows[0] if okr_org_rows else None
        okr_items = SUPA.table("okr_items").select("*").eq("person", person).execute().data
        role_data = gather_role_data(person, role)

        if role == "sales":
            check_sales_meetings(person, role_data.get("sales_accounts", []))

        suggestions = call_claude_analysis(person, okr_org_row, okr_items, role_data, drive_files, role)
        for s in suggestions:
            text = (s.get("suggestion") or "").strip()
            if not text:
                continue
            if s.get("suggestion_type") == "drive_link" and s.get("is_personal_existing_match"):
                # 본인의 기존 레코드를 보완하는 것뿐 — 대표 승인 없이 본인이 바로 확인
                recent_draft = (
                    SUPA.table("ai_drafted_updates").select("id")
                    .eq("person", person).eq("status", "pending").ilike("draft_content", f"%{text[:20]}%")
                    .execute().data
                )
                if recent_draft:
                    continue
                SUPA.table("ai_drafted_updates").insert({
                    "person": person, "source": "google_drive", "source_ref": s.get("drive_file_url"),
                    "target_table": "sales_accounts", "draft_content": text,
                }).execute()
                continue

            recent = (
                SUPA.table("kpi_alignment_suggestions").select("id")
                .eq("person", person).eq("status", "open").ilike("suggestion_text", f"%{text[:20]}%")
                .execute().data
            )
            if recent:
                continue
            SUPA.table("kpi_alignment_suggestions").insert({
                "person": person, "suggestion_text": text,
                "suggestion_type": s.get("suggestion_type") or "kpi_gap",
                "drive_file_name": s.get("drive_file_name"), "drive_file_url": s.get("drive_file_url"),
                "suggest_new_account": bool(s.get("suggest_new_account")),
                "suggested_brand_name": s.get("suggested_brand_name"),
            }).execute()
        print(f"[{person}] 제안 {len(suggestions)}건 처리")

    print(f"[{datetime.now(timezone.utc).isoformat()}] KPI 맥락 정렬 체크 완료")


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════
# Railway Cron 설정
# ══════════════════════════════════════════════════════════
# Cron Schedule: 0 9,18 * * *   (매일 9시, 18시 — 하루 2번)
# Start Command: python scripts/kpi_context_sync.py
# Variables: SUPABASE_URL, SUPABASE_SERVICE_KEY, ANTHROPIC_API_KEY
# (completeness_check.py와 같은 서비스에 두고 Cron을 두 개 등록해도 되고,
#  서비스를 따로 둬도 됩니다 — 둘 다 가벼운 배치 스크립트라 어느 쪽이든 상관없어요.)
#
# ── 구글드라이브 연동(선택) 설정 방법 ──────────────────────
# 1. Google Cloud Console → 프로젝트 선택 → "서비스 계정" 생성
# 2. 그 서비스 계정에 "키 추가 → JSON" → 키 파일 다운로드
# 3. 그 JSON 파일 내용 전체를 Railway 환경변수 GOOGLE_SERVICE_ACCOUNT_JSON 에 붙여넣기
#    (파일이 아니라 "텍스트 내용"을 그대로 붙여넣는 거예요)
# 4. 구글드라이브에서 스캔하고 싶은 폴더 → 공유 → 그 서비스 계정 이메일 주소
#    (xxx@xxx.iam.gserviceaccount.com 형태) 를 "뷰어"로 추가
# 5. 기존에 이미 있는 DRIVE_FOLDER_ID 환경변수가 그 폴더의 ID를 가리키는지 확인
# 6. requirements.txt에 google-api-python-client, google-auth 추가 (메인 레포엔 이미 있음)
#
# 이 설정을 안 해도 나머지 기능(KPI 갭 분석, 조직개선 제안)은 정상 작동합니다 —
# 구글드라이브 쪽만 자동으로 건너뜁니다.
