import os
from datetime import date

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="캠페인 진행 기록", layout="wide", initial_sidebar_state="collapsed")

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

st.title("📸 캠페인 진행 기록")
st.caption("업체별 컨텐츠 배치, 가이드라인 준수, 섭외 지시 — 오늘 진행한 것만 짧게 남겨주세요.")

STAFF_NAMES = ["김선재", "정다영", "양혜준", "구정회", "박솔", "장현우"]
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


def refresh():
    load_placements.clear()
    st.rerun()


my_name = st.selectbox("내 이름", STAFF_NAMES, key="my_name")

st.divider()

# ── 새 배치 등록 ──────────────────────────────────────────
with st.expander("➕ 새 배치 등록", expanded=True):
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
        notes = st.text_area("메모")
        submitted = st.form_submit_button("등록", type="primary", use_container_width=True)

    if submitted:
        if not brand_name.strip() or not influencer_name.strip():
            st.error("브랜드명과 인플루언서명은 꼭 입력해주세요.")
        else:
            SUPA.table("influencer_placements").insert({
                "brand_name": brand_name.strip(), "influencer_name": influencer_name.strip(),
                "category": category, "content_type": content_type,
                "agency_name": agency_name.strip() or None,
                "scheduled_date": scheduled_date.isoformat() if scheduled_date else None,
                "unit_price": unit_price or None,
                "casting_assigned_to": casting_assigned_to.strip() or None,
                "guideline_link": guideline_link.strip() or None,
                "assigned_to": my_name, "notes": notes.strip() or None,
            }).execute()
            st.success("등록 완료!")
            refresh()

st.divider()

# ── 업체별 진행 카드 ──────────────────────────────────────
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
                st.markdown(f"**{p['influencer_name']}** · {p.get('category') or ''} · {p.get('content_type') or ''}  {emoji} {p['status']}")
                meta = []
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
                if p.get("guideline_link"):
                    st.caption(f"📎 가이드라인: {p['guideline_link']}")
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
                    "콘텐츠 링크", value=p.get("content_link") or "", placeholder="업로드된 콘텐츠 링크",
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
