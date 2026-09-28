import os
import io
from datetime import date, timedelta

import pandas as pd
import requests
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="업무보고", layout="wide", initial_sidebar_state="collapsed")

# ── 앱 공통 비밀번호 게이트 (다른 페이지와 동일) ──
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

st.title("📋 업무보고")
st.caption("업체별 컨텐츠 배치, 가이드라인 준수, 섭외 지시, 예정입출금 — 오늘 진행한 것만 짧게 남겨주세요.")

STAFF_NAMES = ["김선재", "곽재선", "구정회", "이단우", "Sanubari", "Meyna"]
CATEGORY_OPTS = ["뷰티", "라이프스타일", "기타"]
CONTENT_TYPE_OPTS = ["PPL", "시딩", "방문형", "캐러셀", "기타"]
STATUS_OPTS = ["섭외중", "섭외완료", "제작중", "업로드완료", "드롭앤고체크완료", "취소"]
STATUS_EMOJI = {
    "섭외중": "🟡", "섭외완료": "🔵", "제작중": "🟠",
    "업로드완료": "🟢", "드롭앤고체크완료": "✅", "취소": "⚪",
}
GUIDELINE_OPTS = ["미확인", "가이드라인 준수", "수정 필요"]
GUIDELINE_TO_BOOL = {"미확인": None, "가이드라인 준수": True, "수정 필요": False}
BOOL_TO_GUIDELINE = {None: "미확인", True: "가이드라인 준수", False: "수정 필요"}

# 구글시트 열 이름 ↔ DB 컬럼 매핑 (실제 직원들이 쓰던 작업시트 헤더 기준)
# 열 이름은 아래 별칭 중 아무거나 써도 인식됨 (대소문자 구분 없음)
SHEET_COL_MAP = {
    "influencer_name": ["Name", "이름", "인플루언서명", "인플루언서", "아이디 이름", "아이디이름", "SNS_ID"],
    "sns_id": ["SNS_ID", "SNS_아이디"],
    "sns_url": ["SNS_URL", "SNS_링크", "인스타링크", "틱톡링크", "프로필링크"],
    "followers": ["팔로워", "팔로워수", "팔로워 수"],
    "email": ["이메일", "이메일주소"],
    "address": ["주소"],
    "shipping_address": ["발송주소", "발송 주소"],
    "phone": ["전화번호", "연락처"],
    "visit_location": ["방문지점", "지점"],
    "visit_date": ["방문날짜", "방문일"],
    "product_name": ["상품", "제품명", "상품명"],
    "content_link": [
        "Upload_URL", "업로드URL", "업로드_URL", "콘텐츠링크", "콘텐츠 링크",
        "업로드 인스타 링크", "업로드 틱톡 링크", "업로드인스타링크", "업로드틱톡링크",
    ],
    "views": ["조회수"],
    "likes": ["좋아요수", "좋아요"],
    "saves": ["저장수"],
    "brand_name": ["브랜드사", "브랜드명", "브랜드", "업체명"],
    "category": ["카테고리", "분류"],
    "content_type": ["콘텐츠유형", "콘텐츠 유형", "유형"],
    "agency_name": ["대행사", "대행사명"],
    "scheduled_date": ["예정일", "예정 날짜"],
    "unit_price": ["단가", "인플루언서단가", "금액"],
    "casting_assigned_to": ["섭외지시대상", "섭외 지시 대상", "섭외담당"],
    "guideline_link": ["가이드라인링크", "가이드라인 링크"],
    "notes": ["메모", "비고"],
}
# 시트 열 이름이 대소문자/공백만 다르게 들어와도 인식되도록, 별칭을 정규화해서 매칭한다
SHEET_COL_MAP_NORM = {
    db_col: [a.strip().lower() for a in aliases] for db_col, aliases in SHEET_COL_MAP.items()
}


def _clean_number(val):
    if val is None:
        return None
    s = str(val).strip().replace(",", "")
    if not s or s in ("-", "–", "N/A", "n/a"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _clean_date(val):
    if val is None or str(val).strip() == "":
        return None
    s = str(val).strip().replace(".", "-").rstrip("-")
    try:
        return pd.to_datetime(s).date().isoformat()
    except Exception:
        return None


_ALL_ALIASES_NORM = set()
for _aliases in SHEET_COL_MAP_NORM.values():
    _ALL_ALIASES_NORM.update(_aliases)


def _find_header_rows(raw_df, min_matches=2):
    """시트 안에 섹션이 여러 개 있어서 헤더가 1행이 아닐 수도 있으니,
    알려진 헤더 이름이 2개 이상 매치되는 행을 전부 헤더 후보로 찾는다."""
    header_rows = []
    for i in range(len(raw_df)):
        cells_norm = [str(c).strip().lower() for c in raw_df.iloc[i].tolist() if str(c).strip() and str(c) != "nan"]
        if sum(1 for c in cells_norm if c in _ALL_ALIASES_NORM) >= min_matches:
            header_rows.append(i)
    return header_rows


def _parse_multi_section_sheet(raw_df):
    """찾은 헤더 행들을 기준으로 시트를 여러 섹션으로 나눠서, 섹션마다 각자의 헤더로 파싱한다."""
    header_rows = _find_header_rows(raw_df)
    if not header_rows:
        # 헤더 후보를 하나도 못 찾으면, 그냥 1행을 헤더로 보고 기존 방식대로 시도
        raw_df.columns = [str(c).strip() for c in raw_df.iloc[0].tolist()]
        return [_map_sheet_row(r) for _, r in raw_df.iloc[1:].iterrows()]

    all_rows = []
    for idx, hidx in enumerate(header_rows):
        end = header_rows[idx + 1] if idx + 1 < len(header_rows) else len(raw_df)
        header = [str(x).strip() for x in raw_df.iloc[hidx].tolist()]
        section_df = raw_df.iloc[hidx + 1: end].copy()
        section_df.columns = header
        for _, r in section_df.iterrows():
            non_empty = sum(1 for v in r if pd.notna(v) and str(v).strip())
            if non_empty < 2:
                continue  # 구분줄/섹션 제목줄처럼 내용이 거의 없는 행은 건너뜀
            mapped = _map_sheet_row(r)
            if mapped.get("influencer_name"):
                all_rows.append(mapped)
    return all_rows


@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()


@st.cache_data(ttl=20)
def load_placements():
    return SUPA.table("influencer_placements").select("*").order("created_at", desc=True).execute().data


@st.cache_data(ttl=20)
def load_activity_log(limit=15):
    return SUPA.table("daily_activity_log").select("*").order("created_at", desc=True).limit(limit).execute().data


@st.cache_data(ttl=20)
def load_forecasts():
    return SUPA.table("fin_cash_forecasts").select("*").order("created_at", desc=True).limit(20).execute().data


def refresh():
    load_placements.clear()
    load_activity_log.clear()
    load_forecasts.clear()
    st.rerun()


my_name = st.selectbox("내 이름", STAFF_NAMES, key="my_name")

st.divider()

# ══════════════════════════════════════════════════════════
# 🖊️ 오늘 빠른 기록
# ══════════════════════════════════════════════════════════
st.subheader("🖊️ 오늘 빠른 기록")
st.caption("특정 브랜드/인플루언서 카드를 안 만들어도, 그냥 오늘 뭐 했는지 한 줄만 남기면 됩니다.")
with st.form("quick_log_form", clear_on_submit=True):
    qc1, qc2 = st.columns([4, 1])
    quick_note = qc1.text_input("오늘 한 일", placeholder="예: 사누바리한테 왕홍 5명 섭외 지시함", label_visibility="collapsed")
    quick_submitted = qc2.form_submit_button("기록", type="primary", use_container_width=True)
if quick_submitted:
    if quick_note.strip():
        SUPA.table("daily_activity_log").insert({"staff_name": my_name, "note": quick_note.strip()}).execute()
        st.success("기록 완료!")
        refresh()
    else:
        st.error("한 줄이라도 적어주세요.")

recent_logs = load_activity_log()
if recent_logs:
    with st.expander(f"최근 기록 {len(recent_logs)}건 보기"):
        for lg in recent_logs:
            when = lg["created_at"][:16].replace("T", " ")
            st.caption(f"{when} · **{lg['staff_name']}** · {lg['note']}")

st.divider()

# ══════════════════════════════════════════════════════════
# 📥 구글시트로 일괄 등록
# ══════════════════════════════════════════════════════════
st.subheader("📥 구글시트로 일괄 등록")
st.caption(
    "여러 건을 한 번에 올리고 싶으면, 구글시트 링크를 붙여넣으세요. "
    "시트는 '링크가 있는 모든 사용자 - 뷰어'로 공유 설정만 해두면 됩니다. "
    "지금 쓰시는 작업시트 헤더(Name, SNS_ID, SNS_URL, 방문지점, 방문날짜, 상품, Upload_URL, 조회수, 좋아요수, 저장수, 브랜드사) 그대로 인식돼요. "
    "브랜드사 칸에 여러 브랜드가 콤마로 같이 있으면 브랜드별로 자동으로 나눠서 등록됩니다. Upload_URL이 있으면 상태도 자동으로 '업로드완료'로 잡혀요."
)


def _sheet_csv_url(sheet_url):
    if "/spreadsheets/d/" not in sheet_url:
        return None
    sheet_id = sheet_url.split("/spreadsheets/d/")[1].split("/")[0]
    gid = "0"
    if "gid=" in sheet_url:
        gid = sheet_url.split("gid=")[1].split("&")[0].split("#")[0]
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _map_sheet_row(row):
    mapped = {}
    row_index_norm = {str(c).strip().lower(): c for c in row.index}
    for db_col, aliases_norm in SHEET_COL_MAP_NORM.items():
        for alias_norm in aliases_norm:
            if alias_norm in row_index_norm:
                real_col = row_index_norm[alias_norm]
                val = row[real_col]
                if pd.notna(val) and str(val).strip():
                    mapped[db_col] = str(val).strip()
                    break
    return mapped


gc1, gc2 = st.columns([4, 1])
sheet_url = gc1.text_input("구글시트 링크", placeholder="https://docs.google.com/spreadsheets/d/...", label_visibility="collapsed")
load_clicked = gc2.button("불러오기", use_container_width=True)
default_brand = st.text_input(
    "이 시트 전체에 적용할 기본 브랜드명 (행에 브랜드사 칸이 없으면 이걸로 채워요, 예: 기자단 섹션)",
    placeholder="예: OWM",
)

if load_clicked:
    if not sheet_url.strip():
        st.error("링크를 붙여넣어주세요.")
    else:
        csv_url = _sheet_csv_url(sheet_url.strip())
        if not csv_url:
            st.error("구글시트 링크 형식이 아닌 것 같아요. 시트를 열어서 주소창의 링크를 그대로 복사해주세요.")
        else:
            try:
                res = requests.get(csv_url, timeout=15)
                if res.status_code != 200:
                    raise ValueError(f"응답 코드 {res.status_code}")
                raw_df = pd.read_csv(io.StringIO(res.text), header=None, dtype=str)
                if raw_df.empty:
                    st.warning("시트에서 데이터를 찾지 못했습니다.")
                else:
                    raw_rows = _parse_multi_section_sheet(raw_df)
                    if not raw_rows:
                        st.warning("인플루언서명(이름/Name/아이디 이름 등)을 인식하지 못했습니다. 시트 헤더 이름을 확인해주세요.")
                    # 브랜드사가 없는 행(예: 기자단 섹션)은 기본 브랜드명으로 채움
                    for r in raw_rows:
                        if not r.get("brand_name"):
                            r["brand_name"] = default_brand.strip() or "미지정"
                    # 브랜드사 칸에 "닥터리엔장,텔로엑트,옵티팜" 처럼 여러 브랜드가 콤마로 같이 들어있으면
                    # 브랜드별로 한 건씩 쪼개서 저장한다 (업체별 배치 현황을 정확히 보기 위함)
                    rows = []
                    for r in raw_rows:
                        brand_field = r["brand_name"]
                        brand_list = [b.strip("() ") for b in brand_field.replace("、", ",").split(",") if b.strip("() ")]
                        for b in (brand_list or [brand_field]):
                            row_copy = dict(r)
                            row_copy["brand_name"] = b
                            rows.append(row_copy)
                    st.session_state["sheet_rows_preview"] = rows
                    st.success(f"{len(rows)}건 인식됨 (섹션 {len(_find_header_rows(raw_df))}개 감지). 아래에서 확인 후 등록하세요.")
            except Exception as e:
                st.error(
                    f"시트를 못 읽었어요 ({e}). 구글시트가 '링크가 있는 모든 사용자 - 뷰어'로 공유되어 있는지 확인해주세요."
                )

preview_rows = st.session_state.get("sheet_rows_preview")
if preview_rows:
    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
    if st.button(f"✅ 이 {len(preview_rows)}건 일괄 등록", type="primary"):
        for r in preview_rows:
            content_link = r.get("content_link")
            payload = {
                "brand_name": r.get("brand_name"), "influencer_name": r.get("influencer_name"),
                "category": r.get("category") if r.get("category") in CATEGORY_OPTS else "기타",
                "content_type": r.get("content_type") if r.get("content_type") in CONTENT_TYPE_OPTS else "기타",
                "agency_name": r.get("agency_name"),
                "sns_id": r.get("sns_id"), "sns_url": r.get("sns_url"),
                "product_name": r.get("product_name"), "visit_location": r.get("visit_location"),
                "visit_date": _clean_date(r.get("visit_date")),
                "scheduled_date": _clean_date(r.get("scheduled_date")),
                "unit_price": _clean_number(r.get("unit_price")),
                "views": _clean_number(r.get("views")), "likes": _clean_number(r.get("likes")),
                "saves": _clean_number(r.get("saves")), "followers": _clean_number(r.get("followers")),
                "email": r.get("email"), "address": r.get("address"),
                "shipping_address": r.get("shipping_address"), "phone": r.get("phone"),
                "casting_assigned_to": r.get("casting_assigned_to"),
                "guideline_link": r.get("guideline_link"),
                "content_link": content_link,
                "status": "업로드완료" if content_link else "섭외중",
                "actual_upload_date": date.today().isoformat() if content_link else None,
                "notes": r.get("notes"),
                "assigned_to": my_name,
            }
            SUPA.table("influencer_placements").insert(payload).execute()
        st.session_state.pop("sheet_rows_preview", None)
        st.success(f"{len(preview_rows)}건 등록 완료!")
        refresh()

st.divider()

# ══════════════════════════════════════════════════════════
# ➕ 새 배치 등록 (개별, 계약서 첨부 포함)
# ══════════════════════════════════════════════════════════
with st.expander("➕ 새 배치 등록 (업체·인플루언서가 정해졌을 때)"):
    with st.form("new_placement_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        brand_name = c1.text_input("브랜드명 *")
        influencer_name = c2.text_input("인플루언서명 *")

        c3, c4, c5 = st.columns(3)
        category = c3.selectbox("카테고리", CATEGORY_OPTS)
        content_type = c4.selectbox("콘텐츠 유형", CONTENT_TYPE_OPTS)
        agency_name = c5.text_input("대행사(있으면)")

        c6, c7, c8 = st.columns(3)
        scheduled_date = c6.date_input("예정일", value=None)
        unit_price = c7.number_input("단가(있으면)", min_value=0, step=10000, format="%d")
        casting_assigned_to = c8.text_input("섭외 지시 대상", placeholder="예: 사누바리, 현지 staff 이름")

        guideline_link = st.text_input("가이드라인 링크(있으면)", placeholder="브리핑/가이드라인 문서 URL")
        contract_file = st.file_uploader("업체 계약서 최종본 첨부(있으면)", type=["pdf", "docx", "png", "jpg"])
        notes = st.text_area("메모")
        submitted = st.form_submit_button("등록", type="primary", use_container_width=True)

    if submitted:
        if not brand_name.strip() or not influencer_name.strip():
            st.error("브랜드명과 인플루언서명은 꼭 입력해주세요.")
        else:
            contract_url = None
            if contract_file is not None:
                try:
                    path = f"{brand_name.strip()}/{int(date.today().strftime('%Y%m%d'))}_{contract_file.name}"
                    SUPA.storage.from_("contract-files").upload(
                        path, contract_file.getvalue(),
                        {"content-type": contract_file.type or "application/octet-stream"},
                    )
                    base = os.environ.get("SUPABASE_URL")
                    contract_url = f"{base}/storage/v1/object/public/contract-files/{path}"
                except Exception as e:
                    st.warning(f"계약서 업로드는 실패했지만 나머지 내용은 등록할게요 ({e})")

            SUPA.table("influencer_placements").insert({
                "brand_name": brand_name.strip(), "influencer_name": influencer_name.strip(),
                "category": category, "content_type": content_type,
                "agency_name": agency_name.strip() or None,
                "scheduled_date": scheduled_date.isoformat() if scheduled_date else None,
                "unit_price": unit_price or None,
                "casting_assigned_to": casting_assigned_to.strip() or None,
                "guideline_link": guideline_link.strip() or None,
                "contract_file_url": contract_url,
                "assigned_to": my_name, "notes": notes.strip() or None,
            }).execute()
            st.success("등록 완료!")
            refresh()

st.divider()

# ══════════════════════════════════════════════════════════
# 📋 업체별 진행 현황
# ══════════════════════════════════════════════════════════
st.subheader("📋 업체별 진행 현황")
fc1, fc2 = st.columns(2)
show_all = fc1.checkbox("전체 담당자 보기 (기본: 내 담당만)", value=False)
show_done = fc2.checkbox("완료/취소 건도 보기", value=False)

placements = load_placements()
items = placements if show_all else [p for p in placements if p["assigned_to"] == my_name]
if not show_done:
    items = [p for p in items if p["status"] not in ("드롭앤고체크완료", "취소")]

st.caption(f"{len(items)}건")

brands = sorted(set(p["brand_name"] for p in items))
for brand in brands:
    brand_items = [p for p in items if p["brand_name"] == brand]
    with st.expander(f"🏢 **{brand}** ({len(brand_items)}건)", expanded=True):
        for p in brand_items:
            with st.container(border=True):
                emoji = STATUS_EMOJI.get(p["status"], "⚪")
                sns = f" (@{p['sns_id']})" if p.get("sns_id") else ""
                st.markdown(f"**{p['influencer_name']}**{sns} · {p.get('category') or ''} · {p.get('content_type') or ''}  {emoji} {p['status']}")
                meta = []
                if p.get("product_name"):
                    meta.append(f"상품: {p['product_name']}")
                if p.get("visit_location"):
                    meta.append(f"방문지점: {p['visit_location']}")
                if p.get("visit_date"):
                    meta.append(f"방문일: {p['visit_date']}")
                if p.get("agency_name"):
                    meta.append(f"대행사: {p['agency_name']}")
                if p.get("assigned_to"):
                    meta.append(f"담당: {p['assigned_to']}")
                if p.get("casting_assigned_to"):
                    meta.append(f"섭외지시: {p['casting_assigned_to']}")
                if p.get("scheduled_date"):
                    meta.append(f"예정일: {p['scheduled_date']}")
                if meta:
                    st.caption(" · ".join(meta))
                if any(p.get(k) for k in ("views", "likes", "saves")):
                    st.caption(f"📈 조회수 {p.get('views') or 0:,} · 좋아요 {p.get('likes') or 0:,} · 저장 {p.get('saves') or 0:,}")
                if p.get("followers"):
                    st.caption(f"👥 팔로워 {p['followers']:,}")
                if p.get("shipping_address") or p.get("phone"):
                    st.caption(f"📦 발송: {p.get('shipping_address') or ''} {p.get('phone') or ''}")
                if p.get("sns_url"):
                    st.caption(f"🔗 SNS: {p['sns_url']}")
                if p.get("guideline_link"):
                    st.caption(f"📎 가이드라인: {p['guideline_link']}")
                if p.get("contract_file_url"):
                    st.caption(f"📄 계약서: {p['contract_file_url']}")
                if p.get("notes"):
                    st.caption(f"메모: {p['notes']}")

                cc1, cc2, cc3 = st.columns([1.1, 1.1, 2.3])
                new_status = cc1.selectbox(
                    "상태", STATUS_OPTS, index=STATUS_OPTS.index(p["status"]) if p["status"] in STATUS_OPTS else 0,
                    key=f"status_{p['id']}", label_visibility="collapsed",
                )
                cur_guideline_label = BOOL_TO_GUIDELINE.get(p.get("guideline_ok"), "미확인")
                new_guideline_label = cc2.selectbox(
                    "가이드라인", GUIDELINE_OPTS, index=GUIDELINE_OPTS.index(cur_guideline_label),
                    key=f"guideline_{p['id']}", label_visibility="collapsed",
                )
                new_link = cc3.text_input(
                    "콘텐츠 링크", value=p.get("content_link") or "", placeholder="업로드된 콘텐츠 링크(=성과보고 링크)",
                    key=f"link_{p['id']}", label_visibility="collapsed",
                )
                if st.button("저장", key=f"save_{p['id']}", use_container_width=True):
                    update_payload = {
                        "status": new_status, "content_link": new_link or None,
                        "guideline_ok": GUIDELINE_TO_BOOL[new_guideline_label],
                    }
                    if new_status == "업로드완료" and not p.get("actual_upload_date"):
                        update_payload["actual_upload_date"] = date.today().isoformat()
                    SUPA.table("influencer_placements").update(update_payload).eq("id", p["id"]).execute()
                    st.success("저장 완료")
                    refresh()

st.divider()

# ══════════════════════════════════════════════════════════
# 📢 예정입출금 신고 (구 12_지출예정보고 통합)
# ══════════════════════════════════════════════════════════
st.subheader("📢 예정입출금 신고")
st.caption("\"이런 돈이 곧 나갈/들어올 것 같다\" 싶은 게 있으면 짧게 남겨주세요. 재무캘린더 대시보드에 바로 반영돼요.")

with st.form("cash_forecast_form", clear_on_submit=True):
    fcc1, fcc2, fcc3 = st.columns(3)
    direction_label = fcc1.radio("구분", ["지출 예정", "수입 예정"], horizontal=True)
    fc_amount = fcc2.number_input("예상 금액", min_value=0, step=10000, format="%d")
    fc_date = fcc3.date_input("예상 날짜", value=date.today())
    fc_reason = st.text_area("무슨 건인지 짧게 적어주세요", placeholder="예: OO브랜드 8월 캠페인 잔금")
    fc_submitted = st.form_submit_button("📝 등록하기", type="primary", use_container_width=True)

if fc_submitted:
    if fc_amount <= 0:
        st.error("금액을 입력해주세요.")
    elif not fc_reason.strip():
        st.error("무슨 건인지 간단히 적어주세요.")
    else:
        SUPA.table("fin_cash_forecasts").insert({
            "direction": "out" if direction_label == "지출 예정" else "in",
            "amount": fc_amount, "expected_date": fc_date.isoformat(),
            "reason": fc_reason.strip(), "submitted_by": my_name, "status": "open",
        }).execute()
        st.success("등록 완료!")
        refresh()

recent_fc = load_forecasts()
if recent_fc:
    with st.expander(f"최근 신고 {len(recent_fc)}건 보기"):
        for f in recent_fc:
            status_label = {"open": "🟡 대기중", "resolved": "✅ 처리완료", "dismissed": "🗑️ 취소됨"}.get(f["status"], f["status"])
            direction_kr = "지출" if f["direction"] == "out" else "수입"
            st.caption(f"{f['expected_date']} · {direction_kr} · ₩{float(f['amount']):,.0f} · {status_label} · {f.get('submitted_by') or ''} · {f.get('reason') or ''}")

st.divider()

# ══════════════════════════════════════════════════════════
# 📊 내 요약 카드 (OKR 대체 — 읽기 전용, 자동 집계)
# ══════════════════════════════════════════════════════════
st.subheader("📊 내 요약 카드")
st.caption("직접 채우는 표가 아니라, 위에서 남긴 기록들이 자동으로 정리되는 카드예요. 관리자와 소통할 때 참고용으로 쓰세요.")

week_ago = pd.Timestamp.now() - timedelta(days=7)
my_placements = [p for p in placements if p["assigned_to"] == my_name]
my_logs_all = SUPA.table("daily_activity_log").select("*").eq("staff_name", my_name).order("created_at", desc=True).limit(50).execute().data

status_counts = {s: 0 for s in STATUS_OPTS}
for p in my_placements:
    status_counts[p["status"]] = status_counts.get(p["status"], 0) + 1

new_this_week = sum(1 for p in my_placements if pd.Timestamp(p["created_at"]) >= week_ago)
done_this_week = sum(
    1 for p in my_placements
    if p.get("actual_upload_date") and pd.Timestamp(p["actual_upload_date"]) >= week_ago
)
logs_this_week = sum(1 for lg in my_logs_all if pd.Timestamp(lg["created_at"]) >= week_ago)

m1, m2, m3, m4 = st.columns(4)
m1.metric("이번주 신규 등록", f"{new_this_week}건")
m2.metric("이번주 업로드 완료", f"{done_this_week}건")
m3.metric("이번주 빠른기록", f"{logs_this_week}건")
m4.metric("전체 진행중", f"{sum(v for k, v in status_counts.items() if k not in ('드롭앤고체크완료', '취소'))}건")

st.markdown("**상태별 현황**")
st.bar_chart(pd.Series(status_counts))
