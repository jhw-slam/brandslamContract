import os
import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="브랜드슬램 계약 현황", layout="wide")

# ── 내부용 비밀번호 게이트 ────────────────────────────────────
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ── Supabase (service_role · 서버사이드 전용) ─────────────────
@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ Supabase 환경변수가 설정되지 않았습니다.\nSUPABASE_URL, SUPABASE_SERVICE_KEY를 설정해주세요.")
        st.stop()
    return create_client(url, key)
SUPA = sb()

STATUS_LABEL = {"PAYMENT_PENDING": "결제 대기", "KICKOFF": "진행중",
                "IN_PROGRESS": "진행중", "COMPLETED": "완료"}
DONE = {"COMPLETED"}
won = lambda n: "₩{:,}".format(int(n or 0))

@st.cache_data(ttl=60)
def load():
    rows = SUPA.table("campaigns").select(
        "order_number,brand_name,product_name,plan,status,plan_price,"
        "start_date,end_date,customer_name,created_at"
    ).order("created_at", desc=True).execute().data
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["plan_price"] = pd.to_numeric(df["plan_price"], errors="coerce").fillna(0).astype(int)
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
    df["연"] = df["created_at"].dt.year
    df["월"] = df["created_at"].dt.strftime("%Y-%m")
    df["상태"] = df["status"].map(lambda s: STATUS_LABEL.get(s, s))
    df["업체"] = df["brand_name"].fillna("(미상)")
    return df

df = load()
st.title("브랜드슬램 · 계약 현황")
if df.empty:
    st.info("campaigns 데이터가 없습니다."); st.stop()

# ── 상단 KPI ──────────────────────────────────────────────────
k = st.columns(4)
k[0].metric("총 계약", f"{len(df)}건")
k[1].metric("총 금액", won(df["plan_price"].sum()))
k[2].metric("진행중", f"{int((~df['status'].isin(DONE)).sum())}건")
k[3].metric("완료", f"{int(df['status'].isin(DONE).sum())}건")

mode = st.sidebar.radio("보기 모드",
    ["업체별 현황", "전체 계약 목록", "지난(완료) 계약", "월별 집계", "연별 집계", "금액 요약"])

COLS = ["created_at", "업체", "product_name", "plan", "상태", "plan_price", "customer_name", "order_number"]
REN  = {"created_at": "일자", "product_name": "상품", "plan": "플랜",
        "plan_price": "금액", "customer_name": "담당자", "order_number": "주문번호"}

def table(frame):
    t = frame[COLS].rename(columns=REN).copy()
    t["일자"] = t["일자"].dt.strftime("%Y-%m-%d")
    t["금액"] = t["금액"].map(won)
    st.dataframe(t, use_container_width=True, hide_index=True)

# ── 연도 필터 (해당 모드에서만) ───────────────────────────────
years = sorted(df["연"].dropna().astype(int).unique().tolist(), reverse=True)
if mode in ("전체 계약 목록", "지난(완료) 계약", "월별 집계"):
    ysel = st.sidebar.multiselect("연도", years, default=years)
    view = df[df["연"].isin(ysel)]
else:
    view = df

if mode == "업체별 현황":
    g = (df.groupby("업체")
           .agg(건수=("order_number", "count"), 총액=("plan_price", "sum"), 최근=("created_at", "max"))
           .sort_values("총액", ascending=False).reset_index())
    disp = g.copy(); disp["총액"] = disp["총액"].map(won); disp["최근"] = disp["최근"].dt.strftime("%Y-%m-%d")
    st.dataframe(disp, use_container_width=True, hide_index=True)
    for brand in g["업체"]:
        sub = df[df["업체"] == brand]
        with st.expander(f"{brand} · {len(sub)}건 · {won(sub['plan_price'].sum())}"):
            table(sub)

elif mode in ("전체 계약 목록", "지난(완료) 계약"):
    v = view if mode == "전체 계약 목록" else view[view["status"].isin(DONE)]
    table(v)
    st.caption(f"{len(v)}건 · 합계 {won(v['plan_price'].sum())}")

elif mode == "월별 집계":
    g = (view.groupby("월").agg(건수=("order_number", "count"), 금액=("plan_price", "sum"))
             .reset_index().sort_values("월"))
    st.bar_chart(g.set_index("월")["금액"])
    d = g.copy(); d["금액"] = d["금액"].map(won)
    st.dataframe(d, use_container_width=True, hide_index=True)

elif mode == "연별 집계":
    g = (df.groupby("연").agg(건수=("order_number", "count"), 금액=("plan_price", "sum"))
           .reset_index().sort_values("연"))
    st.bar_chart(g.set_index("연")["금액"])
    d = g.copy(); d["금액"] = d["금액"].map(won)
    st.dataframe(d, use_container_width=True, hide_index=True)

elif mode == "금액 요약":
    c = st.columns(3)
    c[0].metric("총액", won(df["plan_price"].sum()))
    c[1].metric("진행중 금액", won(df[~df["status"].isin(DONE)]["plan_price"].sum()))
    c[2].metric("완료 금액", won(df[df["status"].isin(DONE)]["plan_price"].sum()))
    st.subheader("상태별")
    s = df.groupby("상태").agg(건수=("order_number", "count"), 금액=("plan_price", "sum")).reset_index()
    s["금액"] = s["금액"].map(won)
    st.dataframe(s, use_container_width=True, hide_index=True)
    st.subheader("업체별 금액")
    b = df.groupby("업체").agg(금액=("plan_price", "sum")).sort_values("금액", ascending=False).reset_index()
    b["금액"] = b["금액"].map(won)
    st.dataframe(b, use_container_width=True, hide_index=True)
