import os
from datetime import date, datetime

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="예정입출금 등록", layout="centered")

# ── 앱 공통 비밀번호 게이트 (다른 페이지와 동일 — 직원 누구나 접근 가능) ──
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

st.title("📢 예정입출금 등록")
st.caption("\"이런 돈이 곧 나갈/들어올 것 같다\" 싶은 게 있으면 짧게 남겨주세요. 계약서 작성 없이도 예측용으로 가볍게 쓰는 화면입니다. 재무캘린더 대시보드에 바로 반영돼요.")

STAFF_NAMES = ["김선재", "이단우", "구정회","장현우"]


@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()

with st.form("cash_forecast_form", clear_on_submit=True):
    name = st.selectbox("작성자", STAFF_NAMES)
    direction_label = st.radio("구분", ["돈이 나갈 예정 (지출)", "돈이 들어올 예정 (수입)"], horizontal=False)
    amount = st.number_input("예상 금액", min_value=0, step=10000, format="%d")
    expected_date = st.date_input("예상 날짜", value=date.today())
    reason = st.text_area("무슨 건인지 짧게 적어주세요", placeholder="예: OO브랜드 8월 캠페인 잔금, 다음주 화요일 인플루언서 지급 예정 등")
    submitted = st.form_submit_button("📝 등록하기", type="primary", use_container_width=True)

if submitted:
    if amount <= 0:
        st.error("금액을 입력해주세요.")
    elif not reason.strip():
        st.error("무슨 건인지 간단히 적어주세요.")
    else:
        direction = "out" if direction_label.startswith("돈이 나갈") else "in"
        SUPA.table("fin_cash_forecasts").insert({
            "direction": direction, "amount": amount, "expected_date": expected_date.isoformat(),
            "reason": reason.strip(), "submitted_by": name, "status": "open",
        }).execute()
        st.success("등록 완료! 재무캘린더 대시보드에 바로 반영됩니다.")

st.divider()
st.subheader("📋 최근 등록된 신고 (전체)")
recent = SUPA.table("fin_cash_forecasts").select("*").order("created_at", desc=True).limit(20).execute().data
if not recent:
    st.caption("아직 등록된 신고가 없습니다.")
for f in recent:
    status_label = {"open": "🟡 대기중", "resolved": "✅ 처리완료", "dismissed": "🗑️ 취소됨"}.get(f["status"], f["status"])
    direction_kr = "지출" if f["direction"] == "out" else "수입"
    with st.container(border=True):
        st.markdown(f"**{f['expected_date']}** · {direction_kr} · ₩{float(f['amount']):,.0f} · {status_label}")
        st.caption(f"{f.get('submitted_by') or ''} · {f.get('reason') or ''}")
