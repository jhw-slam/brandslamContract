
import os
from datetime import date

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="손익 현황", layout="wide")

# ── 접근 제어: jhw 전용 ────────────────────────────────────
# 레일웨이 환경변수 이름이 다르면 아래 os.environ.get() 안의 문자열만 바꿔주세요.
JHW_PASSWORD = os.environ.get("JHW_PASSWORD")

if not JHW_PASSWORD:
    st.error("❌ JHW_PASSWORD 환경변수가 설정되어 있지 않습니다. Railway 환경변수를 확인해주세요.")
    st.stop()

if not st.session_state.get("jhw_authenticated"):
    st.title("🔒 손익 현황 - 접근 제한")
    st.caption("이 페이지는 jhw 전용입니다.")
    with st.form("jhw_login_form"):
        pw_input = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("입장")
    if submitted:
        if pw_input == JHW_PASSWORD:
            st.session_state["jhw_authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다.")
        st.stop()
    return create_client(url, key)


SUPA = sb()

st.title("📊 손익(P&L) 현황")
st.caption("발생주의(계약/청구 기준)와 현금주의(실제 입출금 기준)를 함께 확인합니다.")

# ── 데이터 로드 ─────────────────────────────────────────────


@st.cache_data(ttl=300)
def load_data():
    categories = SUPA.table("fin_account_categories").select("*").execute().data
    cash_events = SUPA.table("cash_events").select("*").execute().data
    bank_txns = SUPA.table("bank_transactions").select("*").execute().data
    return (
        pd.DataFrame(categories),
        pd.DataFrame(cash_events),
        pd.DataFrame(bank_txns),
    )


try:
    cat_df, ce_df, bt_df = load_data()
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

if cat_df.empty:
    st.warning("계정과목(fin_account_categories) 데이터가 없습니다.")
    st.stop()

cat_map = cat_df.set_index("id")[["type", "name"]]

TYPE_LABEL = {
    "revenue": "매출",
    "cost_cogs": "매출원가(COGS)",
    "cost_sga": "판관비(SG&A)",
    "labor": "인건비",
    "tax": "세금",
    "other": "기타손익",
}

# ── 기간 필터 ───────────────────────────────────────────────
col_a, col_b, col_c = st.columns([1, 1, 2])
with col_a:
    start_date = st.date_input("시작일", value=None)
with col_b:
    end_date = st.date_input("종료일", value=None)
with col_c:
    basis = st.radio(
        "기준", ["발생주의 (계약/청구)", "현금주의 (실제 입출금)", "둘 다 비교"],
        horizontal=True,
    )


def filter_by_date(df, date_col):
    if df.empty:
        return df
    d = df.copy()
    d[date_col] = pd.to_datetime(d[date_col], errors="coerce")
    if start_date:
        d = d[d[date_col] >= pd.to_datetime(start_date)]
    if end_date:
        d = d[d[date_col] <= pd.to_datetime(end_date)]
    return d


ce_f = filter_by_date(ce_df, "due_date") if not ce_df.empty and "due_date" in ce_df.columns else ce_df
bt_f = filter_by_date(bt_df, "txn_date") if not bt_df.empty and "txn_date" in bt_df.columns else bt_df


# ── 집계 함수 ───────────────────────────────────────────────
def summarize(df, amount_col="amount", direction_col="direction", cat_col="account_category_id"):
    if df.empty or cat_col not in df.columns:
        return pd.DataFrame(columns=["type", "name", "direction", "amount"])
    d = df.copy()
    d["type"] = d[cat_col].map(cat_map["type"])
    d["name"] = d[cat_col].map(cat_map["name"])
    d["type"] = d["type"].fillna("미분류")
    d["name"] = d["name"].fillna("미분류")
    grouped = (
        d.groupby(["type", "name", direction_col])[amount_col]
        .sum()
        .reset_index()
        .rename(columns={direction_col: "direction", amount_col: "amount"})
    )
    return grouped


def pl_summary(grouped):
    revenue = grouped[grouped["type"] == "revenue"]["amount"].sum()
    cogs = grouped[grouped["type"] == "cost_cogs"]["amount"].sum()
    sga = grouped[grouped["type"] == "cost_sga"]["amount"].sum()
    labor = grouped[grouped["type"] == "labor"]["amount"].sum()
    gross_profit = revenue - cogs
    operating_profit = gross_profit - sga - labor
    return {
        "매출": revenue,
        "매출원가": cogs,
        "매출총이익": gross_profit,
        "판관비": sga,
        "인건비": labor,
        "영업이익": operating_profit,
    }


accrual_grouped = summarize(ce_f)
cash_grouped = summarize(bt_f)

accrual_pl = pl_summary(accrual_grouped)
cash_pl = pl_summary(cash_grouped)


def render_pl_table(pl, label):
    st.subheader(label)
    df = pd.DataFrame(
        [{"항목": k, "금액": f"₩{v:,.0f}"} for k, v in pl.items()]
    )
    st.dataframe(df, hide_index=True, use_container_width=True)
    if pl["매출"]:
        margin = pl["매출총이익"] / pl["매출"] * 100
        st.metric("매출총이익률", f"{margin:.1f}%")


st.divider()

if basis == "둘 다 비교":
    c1, c2 = st.columns(2)
    with c1:
        render_pl_table(accrual_pl, "발생주의 (cash_events 기준)")
    with c2:
        render_pl_table(cash_pl, "현금주의 (bank_transactions 기준)")
elif basis.startswith("발생"):
    render_pl_table(accrual_pl, "발생주의 (cash_events 기준)")
else:
    render_pl_table(cash_pl, "현금주의 (bank_transactions 기준)")

# ── 미분류 항목 경고 ────────────────────────────────────────
st.divider()
st.markdown("### ⚠️ 미분류 / 미매칭 점검")

unclassified_ce = ce_f[ce_f["account_category_id"].isna()] if not ce_f.empty and "account_category_id" in ce_f.columns else pd.DataFrame()
unclassified_bt = bt_f[bt_f["account_category_id"].isna()] if not bt_f.empty and "account_category_id" in bt_f.columns else pd.DataFrame()
unmatched_bt = bt_f[bt_f["matched_cash_event_id"].isna()] if not bt_f.empty and "matched_cash_event_id" in bt_f.columns else pd.DataFrame()

col1, col2, col3 = st.columns(3)
col1.metric("cash_events 미분류", len(unclassified_ce))
col2.metric("bank_transactions 미분류", len(unclassified_bt))
col3.metric("은행거래 ↔ cash_events 미매칭", len(unmatched_bt))

if len(unclassified_ce) or len(unclassified_bt):
    with st.expander("미분류 항목 상세 보기"):
        if len(unclassified_ce):
            st.write("**cash_events**")
            st.dataframe(unclassified_ce, use_container_width=True)
        if len(unclassified_bt):
            st.write("**bank_transactions**")
            st.dataframe(unclassified_bt, use_container_width=True)

if len(unmatched_bt):
    with st.expander("은행거래 미매칭 상세 보기"):
        st.dataframe(unmatched_bt, use_container_width=True)

st.caption("데이터는 5분 캐시됩니다. 최신 데이터가 필요하면 페이지를 새로고침하세요.")
