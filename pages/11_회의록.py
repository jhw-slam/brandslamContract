"""
회의록 페이지 — Tiro로 녹음된 회의록을 Supabase에서 불러와 표시.
기존 멀티페이지 Streamlit 앱의 pages/ 폴더에 이 파일을 넣으면
사이드바에 자동으로 "회의록" 페이지가 추가됩니다.
(파일명 앞 번호는 기존 프로젝트의 페이지 정렬 규칙에 맞춰 조정하세요.
 예: pages/5_📝_회의록.py)
"""

import os
from datetime import datetime

import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="회의록", page_icon="📝", layout="wide")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not (SUPABASE_URL and SUPABASE_KEY):
    st.error("SUPABASE_URL / SUPABASE_KEY 환경변수가 설정되어 있지 않습니다.")
    st.stop()

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

st.title("📝 회의록")
st.caption("Tiro(애플워치 녹음) → Claude 요약 → Supabase 저장 파이프라인에서 자동 적재된 회의록입니다.")


@st.cache_data(ttl=60)
def load_companies():
    res = supabase.table("companies").select("id, name").order("name").execute()
    return {row["name"]: row["id"] for row in res.data}


@st.cache_data(ttl=30)
def load_meetings(search: str):
    query = supabase.table("meetings").select("*").order("meeting_date", desc=True)
    res = query.execute()
    rows = res.data or []
    if search:
        s = search.lower()
        rows = [
            r for r in rows
            if s in (r.get("title") or "").lower()
            or s in (r.get("summary") or "").lower()
            or s in (r.get("raw_transcript") or "").lower()
        ]
    return rows


companies = load_companies()

col1, col2 = st.columns([3, 1])
with col1:
    search = st.text_input("검색 (제목 / 요약 / 원문)", "")
with col2:
    if st.button("🔄 새로고침"):
        st.cache_data.clear()

meetings = load_meetings(search)
st.write(f"총 **{len(meetings)}건**")

for m in meetings:
    date_str = m.get("meeting_date") or m.get("created_at") or ""
    try:
        date_str = datetime.fromisoformat(date_str.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass

    with st.expander(f"{date_str} · {m.get('title', '(제목 없음)')}"):
        left, right = st.columns([3, 1])

        with left:
            if m.get("summary"):
                st.markdown("**요약**")
                st.markdown(m["summary"])
            if m.get("action_items"):
                st.markdown("**Action Items**")
                st.markdown(m["action_items"])
            with st.expander("원문 보기"):
                st.text(m.get("raw_transcript", ""))

        with right:
            st.markdown("**연결된 거래처**")
            current_company = next(
                (name for name, cid in companies.items() if cid == m.get("company_id")), "미지정"
            )
            options = ["미지정"] + list(companies.keys())
            selected = st.selectbox(
                "거래처 태그",
                options,
                index=options.index(current_company) if current_company in options else 0,
                key=f"company_{m['id']}",
                label_visibility="collapsed",
            )
            if selected != current_company:
                new_company_id = companies.get(selected)  # "미지정" -> None
                supabase.table("meetings").update({"company_id": new_company_id}).eq("id", m["id"]).execute()
                st.cache_data.clear()
                st.rerun()
