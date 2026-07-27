import os
from datetime import date

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="영업", layout="wide")

# ── 비밀번호 게이트 (기존 페이지와 동일) ─────────────────────────
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()

won = lambda n: "₩{:,}".format(int(n or 0))

# 이 페이지가 처음 실행되기 전, Supabase SQL Editor에서 한 번 실행해야 하는 테이블 정의
TABLE_SQL = """
create table if not exists sales_brands (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  tier text default '일반',              -- '전략' / '일반'
  monthly_budget numeric default 0,       -- 월 마케팅 예산
  marketing_plan text,
  status text default '정상',             -- '정상' / '주의' / '이탈위험'
  satisfaction text default '미확인',      -- '만족' / '보통' / '불만족' / '미확인'
  last_meeting_date date,
  meeting_notes text,
  created_at timestamptz default now()
);

create table if not exists sales_revenue_monthly (
  id uuid primary key default gen_random_uuid(),
  brand_id uuid references sales_brands(id) on delete cascade,
  month date not null,                    -- 매출 발생 월 (예: 2026-07-01)
  revenue numeric default 0,              -- OWM 매출
  created_at timestamptz default now()
);
"""

def load_brands():
    try:
        data = SUPA.table("sales_brands").select("*").order("created_at").execute().data
        return pd.DataFrame(data)
    except Exception:
        return None

def load_revenue():
    try:
        data = SUPA.table("sales_revenue_monthly").select("*").execute().data
        return pd.DataFrame(data)
    except Exception:
        return None

brands_df = load_brands()
rev_df = load_revenue()

st.title("💼 영업 — 마케팅 수주 영업 관리")

if brands_df is None or rev_df is None:
    st.warning("아직 필요한 테이블이 없어요. Supabase SQL Editor에서 아래 쿼리를 한 번만 실행해주세요.")
    st.code(TABLE_SQL, language="sql")
    st.stop()

st.caption("OWM 브랜드 리스트 기반 예산 · 매출 · 마케팅 계획 관리 대시보드")

# ── ① 월별 OWM 매출 추이 (최상단) ────────────────────────────────
st.subheader("월별 OWM 매출 추이")
if not rev_df.empty:
    rev_df["month"] = pd.to_datetime(rev_df["month"])
    monthly = rev_df.groupby("month")["revenue"].sum().reset_index().sort_values("month")
    st.line_chart(monthly.set_index("month")["revenue"])
else:
    st.info("아직 업로드된 매출 데이터가 없어요. 아래 '데이터 업로드' 탭에서 올려주세요.")

st.divider()

# ── ② 대시보드 요약 ─────────────────────────────────────────────
strategic = brands_df[brands_df["tier"] == "전략"] if not brands_df.empty else pd.DataFrame()
general   = brands_df[brands_df["tier"] == "일반"] if not brands_df.empty else pd.DataFrame()

k1, k2, k3, k4 = st.columns(4)
k1.metric("전략브랜드", f"{len(strategic)}개")
k2.metric("일반운영브랜드", f"{len(general)}개")
k3.metric("총 마케팅 예산", won(brands_df["monthly_budget"].sum()) if not brands_df.empty else "₩0")
k4.metric("전체 브랜드 수", f"{len(brands_df)}개")

st.write("")
c1, c2 = st.columns(2)

with c1:
    st.markdown("**매출 상위 브랜드 (최근월 기준)**")
    if not rev_df.empty and not brands_df.empty:
        latest_month = rev_df["month"].max()
        latest = rev_df[rev_df["month"] == latest_month]
        top = latest.merge(brands_df[["id", "name"]], left_on="brand_id", right_on="id")
        top = top.sort_values("revenue", ascending=False).head(5)
        st.dataframe(
            top[["name", "revenue"]].rename(columns={"name": "브랜드", "revenue": "매출"}),
            hide_index=True, use_container_width=True,
        )
    else:
        st.caption("데이터 없음")

with c2:
    st.markdown("**🚨 급한 미팅 필요 (매출은 나는데 마케팅비는 적은 곳)**")
    if not rev_df.empty and not brands_df.empty:
        latest_month = rev_df["month"].max()
        latest = rev_df[rev_df["month"] == latest_month]
        merged = latest.merge(brands_df, left_on="brand_id", right_on="id")
        if len(merged):
            # 기준: 매출 상위 40% 이면서, 마케팅예산이 매출의 3% 미만인 브랜드
            # (기준 수치는 초안값이라 나중에 원하는 값으로 바로 조정 가능)
            rev_threshold = merged["revenue"].quantile(0.6)
            flagged = merged[
                (merged["revenue"] >= rev_threshold)
                & (merged["monthly_budget"] < merged["revenue"] * 0.03)
            ]
            if len(flagged):
                st.dataframe(
                    flagged[["name", "revenue", "monthly_budget"]].rename(
                        columns={"name": "브랜드", "revenue": "매출", "monthly_budget": "마케팅예산"}
                    ),
                    hide_index=True, use_container_width=True,
                )
            else:
                st.caption("해당 없음")
    else:
        st.caption("데이터 없음")

st.divider()

tab1, tab2, tab3 = st.tabs(["📋 브랜드 관리", "📤 데이터 업로드", "🤝 미팅 · 상태체크"])

# ── ③ 브랜드 관리 ────────────────────────────────────────────────
with tab1:
    st.markdown("### 브랜드 목록")
    if not brands_df.empty:
        show = brands_df[["name", "tier", "monthly_budget", "status", "satisfaction", "last_meeting_date"]].rename(
            columns={
                "name": "브랜드", "tier": "구분", "monthly_budget": "마케팅예산",
                "status": "상태", "satisfaction": "만족도", "last_meeting_date": "최근미팅일",
            }
        )
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.caption("등록된 브랜드가 없어요.")

    with st.expander("➕ 새 브랜드 추가"):
        with st.form("add_brand"):
            name = st.text_input("브랜드명")
            tier = st.selectbox("구분", ["전략", "일반"])
            budget = st.number_input("월 마케팅 예산", min_value=0, step=1_000_000)
            plan = st.text_area("마케팅 계획")
            submitted = st.form_submit_button("추가")
            if submitted and name:
                SUPA.table("sales_brands").insert(
                    {"name": name, "tier": tier, "monthly_budget": budget, "marketing_plan": plan}
                ).execute()
                st.success(f"{name} 브랜드를 추가했어요.")
                st.rerun()

# ── ④ 데이터 업로드 ──────────────────────────────────────────────
with tab2:
    st.markdown("### 월별 매출 업로드 (CSV)")
    st.caption("컬럼명: 브랜드명, 월(YYYY-MM), 매출 — 세 컬럼을 포함한 CSV를 올려주세요.")
    file = st.file_uploader("CSV 업로드", type=["csv"])
    if file:
        up = pd.read_csv(file)
        st.dataframe(up.head(), use_container_width=True)
        if st.button("업로드 확정"):
            name_to_id = dict(zip(brands_df["name"], brands_df["id"])) if not brands_df.empty else {}
            rows = []
            skipped = []
            for _, r in up.iterrows():
                bname = str(r["브랜드명"]).strip()
                bid = name_to_id.get(bname)
                if not bid:
                    skipped.append(bname)
                    continue
                rows.append({
                    "brand_id": bid,
                    "month": pd.to_datetime(str(r["월"])).date().isoformat(),
                    "revenue": float(r["매출"]),
                })
            if rows:
                SUPA.table("sales_revenue_monthly").insert(rows).execute()
                st.success(f"{len(rows)}건 업로드 완료")
            if skipped:
                st.warning(f"브랜드 관리 탭에 먼저 등록되지 않아 건너뛴 브랜드: {', '.join(set(skipped))}")
            if rows:
                st.rerun()

# ── ⑤ 미팅 · 상태체크 ────────────────────────────────────────────
with tab3:
    st.markdown("### 미팅 기록 · 상태체크")
    if not brands_df.empty:
        sel = st.selectbox("브랜드 선택", brands_df["name"])
        row = brands_df[brands_df["name"] == sel].iloc[0]
        with st.form("meeting_update"):
            meeting_date = st.date_input(
                "최근 미팅일",
                value=pd.to_datetime(row["last_meeting_date"]).date() if row.get("last_meeting_date") else date.today(),
            )
            notes = st.text_area("미팅 후 메모", value=row.get("meeting_notes") or "")
            status_opts = ["정상", "주의", "이탈위험"]
            satisfaction_opts = ["만족", "보통", "불만족", "미확인"]
            status = st.selectbox("상태", status_opts, index=status_opts.index(row.get("status") or "정상"))
            satisfaction = st.selectbox(
                "만족도", satisfaction_opts, index=satisfaction_opts.index(row.get("satisfaction") or "미확인")
            )
            save = st.form_submit_button("저장")
            if save:
                SUPA.table("sales_brands").update({
                    "last_meeting_date": meeting_date.isoformat(),
                    "meeting_notes": notes,
                    "status": status,
                    "satisfaction": satisfaction,
                }).eq("id", row["id"]).execute()
                st.success("업데이트했어요.")
                st.rerun()
    else:
        st.caption("먼저 '브랜드 관리' 탭에서 브랜드를 등록해주세요.")
