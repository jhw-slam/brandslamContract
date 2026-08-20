import os
from datetime import date, datetime
from collections import defaultdict

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="재무캘린더", layout="wide")

# ── 레이어 1: 앱 공통 비밀번호 게이트 (다른 페이지와 동일) ──────────
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ── 레이어 2: 재무캘린더 전용 게이트 (장현우만 입장 가능) ──────────
FINANCE_ADMIN_EMAIL = "jhw@slam-global.com"
FINANCE_PASSWORD = os.environ.get("FINANCE_PASSWORD")

st.title("💹 재무캘린더")
st.caption("계정과목별 현금흐름 · 미수금/미지급금 · 월별 요약 — 접근 제한 페이지")

if not st.session_state.get("finance_ok"):
    st.warning("🔒 이 페이지는 장현우 전용입니다. 이메일과 비밀번호를 입력해주세요.")
    fc1, fc2 = st.columns(2)
    email_in = fc1.text_input("이메일", placeholder=FINANCE_ADMIN_EMAIL)
    pw_in = fc2.text_input("비밀번호", type="password")
    if st.button("재무캘린더 입장"):
        if not FINANCE_PASSWORD:
            st.error("서버에 FINANCE_PASSWORD 환경변수가 설정되어 있지 않습니다. Railway 환경변수를 추가해주세요.")
        elif email_in.strip().lower() == FINANCE_ADMIN_EMAIL and pw_in == FINANCE_PASSWORD:
            st.session_state.finance_ok = True
            st.rerun()
        else:
            st.error("이메일 또는 비밀번호가 올바르지 않습니다.")
    st.stop()

col_logout = st.columns([6, 1])[1]
if col_logout.button("🔒 잠그기"):
    st.session_state.finance_ok = False
    st.rerun()


# ── Supabase 연결 ──────────────────────────────────────────────
@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()

TYPE_LABELS = {
    "revenue": "매출",
    "cost_cogs": "매출원가",
    "cost_sga": "판관비",
    "labor": "인건비",
    "tax": "세금",
    "other": "영업외/기타",
}
TYPE_ORDER = ["revenue", "cost_cogs", "cost_sga", "labor", "tax", "other"]


@st.cache_data(ttl=60)
def load_data():
    categories = SUPA.table("fin_account_categories").select("*").eq("is_active", True).order("sort_order").execute().data
    events = SUPA.table("cash_events").select(
        "id,project_id,direction,category,title,amount,due_date,paid,paid_date,memo,account_category_id"
    ).execute().data
    projects = SUPA.table("projects").select("id,brand,campaign").execute().data
    return categories, events, projects


def refresh():
    load_data.clear()
    st.rerun()


categories, events, projects = load_data()
cat_by_id = {c["id"]: c for c in categories}
proj_by_id = {p["id"]: p for p in projects}

df = pd.DataFrame(events)
if df.empty:
    st.info("아직 cash_events 데이터가 없습니다.")
    st.stop()

df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce")
df["cat_name"] = df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("name", "미분류"))
df["cat_type"] = df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("type"))
df["brand"] = df["project_id"].map(lambda x: proj_by_id.get(x, {}).get("brand", "-"))
df["month"] = df["due_date"].dt.to_period("M").astype(str)

today = pd.Timestamp(date.today())
this_month = today.strftime("%Y-%m")

# ── 상단 요약 ────────────────────────────────────────────────
st.divider()
m1, m2, m3, m4, m5 = st.columns(5)

paid_in_month = df[(df["direction"] == "in") & (df["paid"] == True) & (df["month"] == this_month)]["amount"].sum()
paid_out_month = df[(df["direction"] == "out") & (df["paid"] == True) & (df["month"] == this_month)]["amount"].sum()
unpaid_in = df[(df["direction"] == "in") & (df["paid"] == False)]["amount"].sum()
unpaid_out = df[(df["direction"] == "out") & (df["paid"] == False)]["amount"].sum()
overdue_in_cnt = df[(df["direction"] == "in") & (df["paid"] == False) & (df["due_date"] < today)].shape[0]

m1.metric("이번달 입금확정", f"₩{paid_in_month:,.0f}")
m2.metric("이번달 지출확정", f"₩{paid_out_month:,.0f}")
m3.metric("이번달 순현금흐름", f"₩{paid_in_month - paid_out_month:,.0f}")
m4.metric("미수금 총액", f"₩{unpaid_in:,.0f}", delta=f"연체 {overdue_in_cnt}건" if overdue_in_cnt else None, delta_color="inverse")
m5.metric("미지급금 총액", f"₩{unpaid_out:,.0f}")

st.divider()

# ── 계정과목별 대시보드 ──────────────────────────────────────
st.subheader("📊 계정과목별 현황")

period_choice = st.radio("기간", ["전체", "이번달", "이번분기"], horizontal=True)
if period_choice == "이번달":
    df_period = df[df["month"] == this_month]
elif period_choice == "이번분기":
    q_start = pd.Timestamp(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    df_period = df[df["due_date"] >= q_start]
else:
    df_period = df

paid_only = st.checkbox("확정(입금/지급완료)된 건만 보기", value=True)
df_view = df_period[df_period["paid"] == True] if paid_only else df_period

for t in TYPE_ORDER:
    sub = df_view[df_view["cat_type"] == t]
    if sub.empty:
        continue
    total = sub["amount"].sum()
    st.markdown(f"**{TYPE_LABELS[t]}** — 합계 ₩{total:,.0f}")
    grp = sub.groupby("cat_name")["amount"].sum().sort_values(ascending=False)
    cc1, cc2 = st.columns([2, 3])
    cc1.dataframe(grp.map(lambda v: f"₩{v:,.0f}"), use_container_width=True)
    cc2.bar_chart(grp)

unclassified = df_view[df_view["cat_name"] == "미분류"]
if not unclassified.empty:
    st.markdown(f"**미분류** — 합계 ₩{unclassified['amount'].sum():,.0f} ({len(unclassified)}건)")

st.divider()

# ── 미수금 / 미지급금 ────────────────────────────────────────
st.subheader("📌 미수금 / 미지급금")

tab_ar, tab_ap = st.tabs(["미수금 (받을 돈)", "미지급금 (줄 돈)"])

with tab_ar:
    ar = df[(df["direction"] == "in") & (df["paid"] == False)].sort_values("due_date")
    if ar.empty:
        st.caption("미수금 없음")
    else:
        show = ar[["due_date", "brand", "cat_name", "title", "amount"]].copy()
        show["연체"] = ar["due_date"].apply(lambda d: "⚠️ 연체" if pd.notna(d) and d < today else "")
        show["amount"] = show["amount"].map(lambda v: f"₩{v:,.0f}")
        show["due_date"] = show["due_date"].dt.strftime("%Y-%m-%d")
        st.dataframe(show, use_container_width=True, hide_index=True)

with tab_ap:
    ap = df[(df["direction"] == "out") & (df["paid"] == False)].sort_values("due_date")
    if ap.empty:
        st.caption("미지급금 없음")
    else:
        show = ap[["due_date", "brand", "cat_name", "title", "amount"]].copy()
        show["연체"] = ap["due_date"].apply(lambda d: "⚠️ 기한초과" if pd.notna(d) and d < today else "")
        show["amount"] = show["amount"].map(lambda v: f"₩{v:,.0f}")
        show["due_date"] = show["due_date"].dt.strftime("%Y-%m-%d")
        st.dataframe(show, use_container_width=True, hide_index=True)

st.divider()

# ── 월별 요약 ────────────────────────────────────────────────
st.subheader("🗓️ 월별 요약")

monthly = df[df["paid"] == True].groupby(["month", "direction"])["amount"].sum().unstack(fill_value=0)
if not monthly.empty:
    monthly = monthly.rename(columns={"in": "입금(확정)", "out": "지출(확정)"})
    for col in ["입금(확정)", "지출(확정)"]:
        if col not in monthly.columns:
            monthly[col] = 0
    monthly["순현금흐름"] = monthly["입금(확정)"] - monthly["지출(확정)"]
    monthly = monthly.sort_index()
    st.dataframe(monthly.style.format("₩{:,.0f}"), use_container_width=True)
    st.bar_chart(monthly[["입금(확정)", "지출(확정)"]])
else:
    st.caption("확정된 내역이 아직 없습니다.")

st.divider()

# ── 미분류 항목 태깅 ─────────────────────────────────────────
st.subheader("🏷️ 미분류 계정과목 태깅")
st.caption("송금캘린더에서 새로 추가된 건은 계정과목이 비어있을 수 있어요. 여기서 지정해주세요. (송금캘린더의 매칭 로직에는 영향 없습니다)")

unclassified_all = df[df["cat_name"] == "미분류"].sort_values("due_date", ascending=False)
if unclassified_all.empty:
    st.success("미분류 항목이 없습니다 👍")
else:
    cat_options = {c["name"]: c["id"] for c in categories}
    for _, row in unclassified_all.iterrows():
        with st.container(border=True):
            rc1, rc2, rc3 = st.columns([3, 1.2, 1.5])
            due_str = row["due_date"].strftime("%Y-%m-%d") if pd.notna(row["due_date"]) else "-"
            rc1.markdown(f"**{row['brand']}** · {due_str} · {'입금' if row['direction']=='in' else '지출'} · ₩{row['amount']:,.0f}\n\n{row['title'] or ''}")
            chosen = rc2.selectbox("계정과목", options=list(cat_options.keys()), key=f"catsel_{row['id']}", label_visibility="collapsed", index=None, placeholder="선택")
            if rc3.button("지정", key=f"assign_{row['id']}"):
                if chosen:
                    SUPA.table("cash_events").update({"account_category_id": cat_options[chosen]}).eq("id", row["id"]).execute()
                    st.success("지정 완료"); refresh()
                else:
                    st.warning("계정과목을 선택해주세요.")
