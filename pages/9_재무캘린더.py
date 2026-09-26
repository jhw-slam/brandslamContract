import os
import io
import re
import json
import base64
import hashlib
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import requests
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
st.caption("계정과목별 현금흐름 · 전체 매칭 관리자 모드 · 실시간 손익계산서 — 접근 제한 페이지")

if FINANCE_PASSWORD and not st.session_state.get("finance_ok"):
    st.warning("🔒 이 페이지는 장현우 전용입니다. 이메일과 비밀번호를 입력해주세요.")
    fc1, fc2 = st.columns(2)
    email_in = fc1.text_input("이메일", placeholder=FINANCE_ADMIN_EMAIL)
    pw_in = fc2.text_input("비밀번호", type="password")
    if st.button("재무캘린더 입장"):
        if email_in.strip().lower() == FINANCE_ADMIN_EMAIL and pw_in == FINANCE_PASSWORD:
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
    "revenue": "매출", "cost_cogs": "매출원가", "cost_sga": "판관비",
    "labor": "인건비", "tax": "세금", "other": "영업외/기타",
    "transfer": "계좌간 이동(내부이체, 손익계산서 제외)",
    "balance_sheet": "재무상태표 항목(자산/부채성, 손익계산서 제외)",
}
TYPE_ORDER = ["revenue", "cost_cogs", "cost_sga", "labor", "tax", "other"]
SETTINGS_TYPE_OPTIONS = TYPE_ORDER + ["transfer", "balance_sheet"]


def compute_pl(pl_df):
    """기간별 bank_df(계정과목 매핑 완료본)를 받아 손익계산서 수치를 계산한다.
    📄 손익계산서 탭과 📝 CFO 인사이트 탭이 공용으로 사용."""
    def line_items(t, expense_side=True):
        sub = pl_df[pl_df["cat_type"] == t]
        grp = sub.groupby(["direction", "cat_name"])["amount"].sum().reset_index()
        result = {}
        for _, r in grp.iterrows():
            sign = 1 if (r["direction"] == "out") == expense_side else -1
            result[r["cat_name"]] = result.get(r["cat_name"], 0) + sign * r["amount"]
        return result

    rev_items = line_items("revenue", expense_side=False)
    cogs_items = line_items("cost_cogs", expense_side=True)
    sga_items = line_items("cost_sga", expense_side=True)
    labor_items = line_items("labor", expense_side=True)
    other_items_raw = pl_df[pl_df["cat_type"] == "other"]

    total_rev = sum(rev_items.values())
    total_cogs = sum(cogs_items.values())
    gross_profit = total_rev - total_cogs
    total_sga = sum(sga_items.values()) + sum(labor_items.values())
    op_profit = gross_profit - total_sga
    other_income = other_items_raw[other_items_raw["direction"] == "in"]["amount"].sum()
    other_expense = other_items_raw[other_items_raw["direction"] == "out"]["amount"].sum()
    pretax = op_profit + other_income - other_expense
    corp_tax = pl_df[(pl_df["cat_name"] == "법인세") & (pl_df["direction"] == "out")]["amount"].sum()
    net_profit = pretax - corp_tax

    combined_sga = {**sga_items}
    for k, v in labor_items.items():
        combined_sga[k] = combined_sga.get(k, 0) + v

    return {
        "rev_items": rev_items, "cogs_items": cogs_items, "combined_sga": combined_sga,
        "total_rev": total_rev, "total_cogs": total_cogs, "gross_profit": gross_profit,
        "total_sga": total_sga, "op_profit": op_profit,
        "other_income": other_income, "other_expense": other_expense,
        "pretax": pretax, "corp_tax": corp_tax, "net_profit": net_profit,
    }


@st.cache_data(ttl=45)
def load_all():
    categories = SUPA.table("fin_account_categories").select("*").order("sort_order").execute().data
    events = SUPA.table("cash_events").select(
        "id,project_id,direction,category,title,amount,due_date,paid,paid_date,memo,account_category_id"
    ).execute().data
    projects = SUPA.table("projects").select("id,brand,campaign").execute().data
    bank_txns = SUPA.table("bank_transactions").select(
        "id,direction,amount,txn_date,txn_datetime,description,matched_cash_event_id,account_label,account_category_id,created_at,dedup_hash"
    ).order("txn_date", desc=True).execute().data
    tax_invs = SUPA.table("tax_invoices").select(
        "approval_no,write_date,issue_date,buyer_biz_no,buyer_name,total_amount,supply_amount,vat,kind,"
        "matched_cash_event_id,canceled,account_category_id"
    ).order("issue_date", desc=True).execute().data
    return categories, events, projects, bank_txns, tax_invs


def refresh():
    load_all.clear()
    st.rerun()


@st.cache_data(ttl=45)
def load_business_notes(active_only=True):
    q = SUPA.table("fin_business_notes").select("*").order("created_at", desc=True)
    if active_only:
        q = q.eq("is_active", True)
    return q.execute().data


def notes_as_prompt_text(notes):
    if not notes:
        return ""
    lines = "\n".join(f"- {n['note']}" + (f" [{n['tag']}]" if n.get("tag") else "") for n in notes)
    return (
        "\n\n참고: 아래는 이 회사의 사업 담당자(대표)가 직접 남겨둔 패턴/맥락 메모다. "
        "다른 어떤 추론보다 이 메모를 우선해서 반영해라:\n" + lines
    )


categories, events, projects, bank_txns, tax_invs = load_all()
cat_by_id = {c["id"]: c for c in categories}
cat_name_to_id = {c["name"]: c["id"] for c in categories if c["is_active"]}
proj_by_id = {p["id"]: p for p in projects}
events_by_id = {e["id"]: e for e in events}

ev_df = pd.DataFrame(events)
if not ev_df.empty:
    ev_df["amount"] = pd.to_numeric(ev_df["amount"], errors="coerce").fillna(0)
    ev_df["due_date"] = pd.to_datetime(ev_df["due_date"], errors="coerce")
    ev_df["cat_name"] = ev_df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("name", "미분류"))
    ev_df["cat_type"] = ev_df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("type"))
    ev_df["brand"] = ev_df["project_id"].map(lambda x: proj_by_id.get(x, {}).get("brand", "-"))
    ev_df["month"] = ev_df["due_date"].dt.to_period("M").astype(str)

def mark_duplicates(df):
    """이미 저장된 은행거래 중 중복 후보를 찾아 표시한다 (DB는 안 바꾸고 화면용 플래그만 계산).
    - is_dup_certain: 계좌+방향+금액+거래일시(초단위)가 완전히 같거나, 시간이 없어도 적요까지 완전히 같은 경우 → 확실한 중복으로 보고 집계에서 제외
    - is_dup_suspect: 계좌+방향+금액+날짜만 같고 적요/시간이 다른 경우 → 애매하니 집계에는 포함하되 화면에 표시만 함
    """
    df = df.copy()
    df["is_dup_certain"] = False
    df["is_dup_suspect"] = False
    df["dup_group"] = None
    if df.empty:
        return df
    df["_sort_key"] = pd.to_datetime(df.get("created_at"), errors="coerce")

    def _flag(group_cols, target_col, require_nonempty_col=None):
        pool = df[~df["is_dup_certain"]] if target_col == "is_dup_certain" else df[~df["is_dup_certain"] & ~df["is_dup_suspect"]]
        grouped = pool.groupby(group_cols, dropna=True).groups
        for gkey, idx in grouped.items():
            idxs = list(idx)
            if len(idxs) < 2:
                continue
            if require_nonempty_col is not None:
                colpos = group_cols.index(require_nonempty_col)
                val = gkey[colpos] if isinstance(gkey, tuple) else gkey
                if not val or (isinstance(val, float) and pd.isna(val)):
                    continue
            sub = df.loc[idxs].sort_values("_sort_key", na_position="last")
            canonical = sub.index[0]
            others = [i for i in idxs if i != canonical]
            if target_col == "is_dup_certain":
                df.loc[others, "is_dup_certain"] = True
            else:
                df.loc[idxs, "is_dup_suspect"] = True
            df.loc[idxs, "dup_group"] = df.loc[idxs, "dup_group"].where(df.loc[idxs, "dup_group"].notna(), str(gkey))

    # A. 계좌+방향+금액+거래일시(초단위)까지 완전히 같음 → 확실한 중복
    if "txn_datetime" in df.columns:
        _flag(["account_label", "direction", "amount", "txn_datetime"], "is_dup_certain")
    # B. 시간정보가 없어도, 계좌+방향+금액+날짜+적요(이름)까지 완전히 같으면 → 확실한 중복
    _flag(["account_label", "direction", "amount", "txn_date", "description"], "is_dup_certain", require_nonempty_col="description")
    # C. 계좌+방향+금액+날짜만 같고 나머지는 다름 → 애매한 중복 의심 (표시만, 집계에서 빼지 않음)
    _flag(["account_label", "direction", "amount", "txn_date"], "is_dup_suspect")

    df.drop(columns=["_sort_key"], inplace=True)
    return df


bank_df = pd.DataFrame(bank_txns)
if not bank_df.empty:
    bank_df["amount"] = pd.to_numeric(bank_df["amount"], errors="coerce").fillna(0)
    bank_df["txn_date"] = pd.to_datetime(bank_df["txn_date"], errors="coerce")
    if "txn_datetime" in bank_df.columns:
        bank_df["txn_datetime"] = pd.to_datetime(bank_df["txn_datetime"], errors="coerce")
    bank_df["cat_name"] = bank_df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("name", "미분류"))
    bank_df["cat_type"] = bank_df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("type"))
    bank_df["matched_brand"] = bank_df["matched_cash_event_id"].map(
        lambda x: (proj_by_id.get(events_by_id.get(x, {}).get("project_id"), {}).get("brand") if pd.notna(x) else None)
    )
    bank_df["matched_title"] = bank_df["matched_cash_event_id"].map(
        lambda x: (events_by_id.get(x, {}).get("title") if pd.notna(x) else None)
    )
    bank_df = mark_duplicates(bank_df)

# 집계(대시보드/손익계산서)는 '확실한 중복'을 제외한 버전을 쓴다 — 실제 매칭/태깅 리스트는 원본(bank_df)을 그대로 보여줌
bank_df_clean = bank_df[~bank_df["is_dup_certain"]].copy() if not bank_df.empty else bank_df

tax_df = pd.DataFrame(tax_invs)
if not tax_df.empty:
    for c in ["total_amount", "supply_amount", "vat"]:
        tax_df[c] = pd.to_numeric(tax_df[c], errors="coerce").fillna(0)
    tax_df["issue_date"] = pd.to_datetime(tax_df["issue_date"], errors="coerce")
    tax_df["cat_name"] = tax_df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("name", "미분류"))

today = pd.Timestamp(date.today())
this_month = today.strftime("%Y-%m")

# ── 상단: 전체 미분류 현황 배너 ──────────────────────────────
unc_bank = int((bank_df_clean["account_category_id"].isna()).sum()) if not bank_df.empty else 0
unc_tax = int((tax_df["account_category_id"].isna()).sum()) if not tax_df.empty else 0
unc_ev = int((ev_df["account_category_id"].isna()).sum()) if not ev_df.empty else 0
total_unc = unc_bank + unc_tax + unc_ev
n_dup_certain = int(bank_df["is_dup_certain"].sum()) if not bank_df.empty else 0
n_dup_suspect = int(bank_df["is_dup_suspect"].sum()) if not bank_df.empty else 0

st.divider()
if total_unc > 0:
    st.warning(f"⚠️ 계정과목 미분류 항목이 총 **{total_unc}건** 있습니다 — 은행거래 {unc_bank} · 세금계산서 {unc_tax} · 송금일정 {unc_ev}  → **'전체 매칭 현황'** 또는 **'AI 계정과목 추천'** 탭에서 처리하세요.")
else:
    st.success("✅ 모든 항목이 계정과목으로 분류되어 있습니다.")
if n_dup_certain or n_dup_suspect:
    st.info(f"🧹 은행거래 중복 의심 — 확실한 중복 {n_dup_certain}건(집계에서 자동 제외됨) · 애매한 중복 {n_dup_suspect}건(표시만, 집계엔 포함) → **'전체 매칭 현황 > 은행거래내역'**의 '중복 거래 정리'에서 확인하세요.")

menu = st.radio(
    "메뉴", ["📊 대시보드", "🔗 전체 매칭 현황", "🤖 AI 계정과목 추천", "🏦 뱅크다 연동", "⚙️ 계정과목 설정", "📄 손익계산서", "📝 CFO 인사이트"],
    horizontal=True, label_visibility="collapsed",
)
st.divider()


# ════════════════════════════════════════════════════════════
# 📊 대시보드
# ════════════════════════════════════════════════════════════
if menu == "📊 대시보드":
    if bank_df.empty:
        st.info("은행거래 데이터가 없습니다.")
        st.stop()

    m1, m2, m3, m4, m5 = st.columns(5)
    paid_in_month = bank_df_clean[(bank_df_clean["direction"] == "in") & (bank_df_clean["txn_date"].dt.strftime("%Y-%m") == this_month)]["amount"].sum()
    paid_out_month = bank_df_clean[(bank_df_clean["direction"] == "out") & (bank_df_clean["txn_date"].dt.strftime("%Y-%m") == this_month)]["amount"].sum()
    unpaid_in = ev_df[(ev_df["direction"] == "in") & (ev_df["paid"] == False)]["amount"].sum() if not ev_df.empty else 0
    unpaid_out = ev_df[(ev_df["direction"] == "out") & (ev_df["paid"] == False)]["amount"].sum() if not ev_df.empty else 0
    overdue_in_cnt = ev_df[(ev_df["direction"] == "in") & (ev_df["paid"] == False) & (ev_df["due_date"] < today)].shape[0] if not ev_df.empty else 0

    m1.metric("이번달 입금(실제)", f"₩{paid_in_month:,.0f}")
    m2.metric("이번달 출금(실제)", f"₩{paid_out_month:,.0f}")
    m3.metric("이번달 순현금흐름", f"₩{paid_in_month - paid_out_month:,.0f}")
    m4.metric("미수금 총액", f"₩{unpaid_in:,.0f}", delta=f"연체 {overdue_in_cnt}건" if overdue_in_cnt else None, delta_color="inverse")
    m5.metric("미지급금 총액", f"₩{unpaid_out:,.0f}")

    st.divider()
    st.subheader("📊 계정과목별 현황 (은행거래 실제 기준, 확실한 중복 제외)")

    period_choice = st.radio("기간", ["전체", "이번달", "이번분기"], horizontal=True, key="dash_period")
    if period_choice == "이번달":
        b_view = bank_df_clean[bank_df_clean["txn_date"].dt.strftime("%Y-%m") == this_month]
    elif period_choice == "이번분기":
        q_start = pd.Timestamp(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
        b_view = bank_df_clean[bank_df_clean["txn_date"] >= q_start]
    else:
        b_view = bank_df_clean

    for t in TYPE_ORDER:
        sub = b_view[b_view["cat_type"] == t]
        if sub.empty:
            continue
        total = sub["amount"].sum()
        st.markdown(f"**{TYPE_LABELS[t]}** — 합계 ₩{total:,.0f}")
        grp = sub.groupby("cat_name")["amount"].sum().sort_values(ascending=False)
        cc1, cc2 = st.columns([2, 3])
        cc1.dataframe(grp.map(lambda v: f"₩{v:,.0f}"), use_container_width=True)
        cc2.bar_chart(grp)

    unclassified = b_view[b_view["cat_name"] == "미분류"]
    if not unclassified.empty:
        st.markdown(f"**미분류** — 합계 ₩{unclassified['amount'].sum():,.0f} ({len(unclassified)}건)")

    internal_transfer = b_view[b_view["cat_type"] == "transfer"]
    if not internal_transfer.empty:
        st.caption(f"🔁 계좌간 자금이동(내부이체) {len(internal_transfer)}건, 합계 ₩{internal_transfer['amount'].sum():,.0f} — 매출/매입 아니므로 위 집계와 손익계산서에서 항상 제외됨")

    bs_items = b_view[b_view["cat_type"] == "balance_sheet"]
    if not bs_items.empty:
        st.caption(f"🏦 재무상태표 항목(보증금·대여금·유형자산 등) {len(bs_items)}건, 합계 ₩{bs_items['amount'].sum():,.0f} — 자산/부채성 항목이라 위 집계와 손익계산서에서 항상 제외됨")

    st.divider()
    st.subheader("📌 미수금 / 미지급금 (송금캘린더 cash_events 기준)")
    tab_ar, tab_ap = st.tabs(["미수금 (받을 돈)", "미지급금 (줄 돈)"])
    with tab_ar:
        ar = ev_df[(ev_df["direction"] == "in") & (ev_df["paid"] == False)].sort_values("due_date") if not ev_df.empty else pd.DataFrame()
        if ar.empty:
            st.caption("미수금 없음")
        else:
            show = ar[["due_date", "brand", "cat_name", "title", "amount"]].copy()
            show["연체"] = ar["due_date"].apply(lambda d: "⚠️ 연체" if pd.notna(d) and d < today else "")
            show["amount"] = show["amount"].map(lambda v: f"₩{v:,.0f}")
            show["due_date"] = show["due_date"].dt.strftime("%Y-%m-%d")
            st.dataframe(show, use_container_width=True, hide_index=True)
    with tab_ap:
        ap = ev_df[(ev_df["direction"] == "out") & (ev_df["paid"] == False)].sort_values("due_date") if not ev_df.empty else pd.DataFrame()
        if ap.empty:
            st.caption("미지급금 없음")
        else:
            show = ap[["due_date", "brand", "cat_name", "title", "amount"]].copy()
            show["연체"] = ap["due_date"].apply(lambda d: "⚠️ 기한초과" if pd.notna(d) and d < today else "")
            show["amount"] = show["amount"].map(lambda v: f"₩{v:,.0f}")
            show["due_date"] = show["due_date"].dt.strftime("%Y-%m-%d")
            st.dataframe(show, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("🗓️ 월별 요약 (은행거래 실제 기준, 확실한 중복 제외)")
    bank_df_clean["month"] = bank_df_clean["txn_date"].dt.to_period("M").astype(str)
    monthly = bank_df_clean.groupby(["month", "direction"])["amount"].sum().unstack(fill_value=0)
    if not monthly.empty:
        monthly = monthly.rename(columns={"in": "입금", "out": "출금"})
        for col in ["입금", "출금"]:
            if col not in monthly.columns:
                monthly[col] = 0
        monthly["순현금흐름"] = monthly["입금"] - monthly["출금"]
        monthly = monthly.sort_index()
        st.dataframe(monthly.style.format("₩{:,.0f}"), use_container_width=True)
        st.bar_chart(monthly[["입금", "출금"]])


# ════════════════════════════════════════════════════════════
# 🔗 전체 매칭 현황
# ════════════════════════════════════════════════════════════
elif menu == "🔗 전체 매칭 현황":
    st.subheader("🔗 전체 매칭 현황 — 관리자 모드")
    st.caption("송금캘린더는 영업 관련 매칭만 처리하고 끝나지만, 여기서는 은행거래·세금계산서·송금일정 전부가 계정과목으로 매칭되어야 합니다.")

    # ── 정합성 점검 (미스매칭 탐지) ──────────────────────────
    with st.expander("⚠️ 정합성 점검 (자동 탐지)", expanded=True):
        issues = []
        if not bank_df.empty and not ev_df.empty:
            for _, bt in bank_df[bank_df["matched_cash_event_id"].notna()].iterrows():
                ev = events_by_id.get(bt["matched_cash_event_id"])
                if ev and abs(float(ev.get("amount") or 0) - float(bt["amount"])) > 1000:
                    issues.append(f"금액 불일치: 은행거래 {bt['txn_date'].date()} ₩{bt['amount']:,.0f} ↔ 매칭된 송금일정 '{ev.get('title') or '-'}' ₩{ev.get('amount'):,.0f}")
        if not tax_df.empty:
            unmatched_tax = tax_df[(tax_df["matched_cash_event_id"].isna()) & (tax_df["canceled"] == False)]
            for _, tv in unmatched_tax.iterrows():
                issues.append(f"미수금 후보: 세금계산서 {tv['issue_date'].date() if pd.notna(tv['issue_date']) else '-'} {tv['buyer_name']} ₩{tv['total_amount']:,.0f} — 연결된 송금일정 없음")
        if issues:
            for i in issues:
                st.markdown(f"- {i}")
        else:
            st.caption("발견된 불일치 없음")

    tab_bank, tab_tax, tab_ev = st.tabs(["💳 은행거래내역", "🧾 세금계산서내역", "📅 송금일정(cash_events)"])

    # cash_event 재연결용 선택지 (라벨 → id), due_date 최신순
    ev_link_opts = ["(연결 해제)"]
    ev_label_to_id = {}
    if not ev_df.empty:
        for _, e in ev_df.sort_values("due_date", ascending=False).iterrows():
            d = e["due_date"].strftime("%Y-%m-%d") if pd.notna(e["due_date"]) else "-"
            label = f"{d} · {e['brand']} · {e['title'] or ''} · ₩{e['amount']:,.0f}"
            ev_link_opts.append(label)
            ev_label_to_id[label] = e["id"]

    # ── 은행거래내역 ──────────────────────────────────────
    with tab_bank:
        n_certain = int(bank_df["is_dup_certain"].sum())
        n_suspect = int(bank_df["is_dup_suspect"].sum())
        if n_certain or n_suspect:
            with st.expander(f"🧹 중복 거래 정리 — 확실한 중복 {n_certain}건 · 애매한 중복 {n_suspect}건", expanded=True):
                if n_certain:
                    st.markdown("**확실한 중복** (계좌+방향+금액+거래일시 초단위까지 같거나, 적요까지 완전히 같음) — 아래 목록은 대시보드/손익계산서 집계에서 이미 자동 제외되어 있습니다. 완전히 지우고 싶으면 '삭제'를 누르세요.")
                    dup_groups = bank_df[bank_df["is_dup_certain"] | (bank_df["dup_group"].isin(bank_df.loc[bank_df["is_dup_certain"], "dup_group"]))]
                    for gkey, g in dup_groups.groupby("dup_group"):
                        if not g["is_dup_certain"].any():
                            continue
                        for _, row in g.sort_values("txn_date").iterrows():
                            tag = "🟡 대표(유지)" if not row["is_dup_certain"] else "🔴 중복(제외됨)"
                            d = row["txn_date"].strftime("%Y-%m-%d") if pd.notna(row["txn_date"]) else "-"
                            dc1, dc2 = st.columns([5, 1])
                            dc1.write(f"{tag} · {'입금' if row['direction']=='in' else '출금'} · {d} · ₩{row['amount']:,.0f} · {row['description'] or ''}")
                            if row["is_dup_certain"] and dc2.button("삭제", key=f"deldup_{row['id']}"):
                                SUPA.table("bank_transactions").delete().eq("id", row["id"]).execute()
                                st.success("삭제 완료"); refresh()
                        st.divider()
                if n_suspect:
                    st.markdown("**애매한 중복 의심** (계좌+방향+금액+날짜는 같은데 적요나 시간이 다름) — 집계에는 그대로 포함되어 있고, 자동으로 지우지 않습니다. 직접 확인 후 필요하면 위 확실한 중복처럼 개별적으로 지워주세요.")
                    for gkey, g in bank_df[bank_df["is_dup_suspect"]].groupby("dup_group"):
                        for _, row in g.sort_values("txn_date").iterrows():
                            d = row["txn_date"].strftime("%Y-%m-%d") if pd.notna(row["txn_date"]) else "-"
                            dc1, dc2 = st.columns([5, 1])
                            dc1.write(f"⚠️ · {'입금' if row['direction']=='in' else '출금'} · {d} · ₩{row['amount']:,.0f} · {row['description'] or ''}")
                            if dc2.button("삭제", key=f"delsus_{row['id']}"):
                                SUPA.table("bank_transactions").delete().eq("id", row["id"]).execute()
                                st.success("삭제 완료"); refresh()
                        st.divider()

        show_only_unc = st.checkbox("미분류만 보기", value=True, key="bank_unc_only")
        base = bank_df[~bank_df["is_dup_certain"]]  # 확실한 중복은 기본 목록에서 항상 숨김
        b_show = base[base["account_category_id"].isna()] if show_only_unc else base
        b_show = b_show.sort_values("txn_date", ascending=False)
        st.caption(f"{len(b_show)}건 · ⚠️ 여기서 재연결하면 송금캘린더의 매칭(완료 상태)에도 즉시 반영됩니다 — 신중하게 사용하세요.")
        cat_opts = ["(선택 안함)"] + list(cat_name_to_id.keys())
        for _, row in b_show.head(150).iterrows():
            with st.container(border=True):
                rc1, rc2, rc3 = st.columns([3.2, 1.4, 1.2])
                match_info = ""
                if pd.notna(row["matched_cash_event_id"]):
                    mb = row["matched_brand"] if pd.notna(row["matched_brand"]) else "-"
                    mt = row["matched_title"] if pd.notna(row["matched_title"]) else ""
                    match_info = f"🔗 매칭됨: {mb} · {mt}"
                sus = " · ⚠️중복의심" if row["is_dup_suspect"] else ""
                xfer = " · 🔁내부이체" if row["cat_type"] == "transfer" else ""
                bsflag = " · 🏦재무상태표항목" if row["cat_type"] == "balance_sheet" else ""
                d = row["txn_date"].strftime("%Y-%m-%d") if pd.notna(row["txn_date"]) else "-"
                rc1.markdown(f"**{'입금' if row['direction']=='in' else '출금'}** · {d} · ₩{row['amount']:,.0f}\n\n{row['description'] or ''}  {match_info}{sus}{xfer}{bsflag}")
                cur_idx = cat_opts.index(row["cat_name"]) if row["cat_name"] in cat_opts else 0
                chosen = rc2.selectbox("계정과목", options=cat_opts, index=cur_idx, key=f"bankcat_{row['id']}", label_visibility="collapsed")
                if rc3.button("저장", key=f"bankassign_{row['id']}"):
                    new_val = cat_name_to_id.get(chosen) if chosen != "(선택 안함)" else None
                    SUPA.table("bank_transactions").update({"account_category_id": new_val}).eq("id", row["id"]).execute()
                    st.success("저장 완료"); refresh()

                with st.expander("🔧 송금일정(cash_event) 재연결"):
                    cur_label = None
                    if pd.notna(row["matched_cash_event_id"]):
                        for lbl, eid in ev_label_to_id.items():
                            if eid == row["matched_cash_event_id"]:
                                cur_label = lbl; break
                    cur_link_idx = ev_link_opts.index(cur_label) if cur_label in ev_link_opts else 0
                    rl1, rl2 = st.columns([4, 1])
                    new_link_label = rl1.selectbox(
                        "연결할 송금일정 선택", options=ev_link_opts, index=cur_link_idx,
                        key=f"relink_{row['id']}", label_visibility="collapsed",
                    )
                    if rl2.button("연결 저장", key=f"relinkbtn_{row['id']}"):
                        new_ev_id = ev_label_to_id.get(new_link_label)  # None이면 연결 해제
                        SUPA.table("bank_transactions").update({"matched_cash_event_id": new_ev_id}).eq("id", row["id"]).execute()
                        st.success("재연결 완료 — 송금캘린더에도 즉시 반영됩니다"); refresh()

    # ── 세금계산서내역 ────────────────────────────────────
    with tab_tax:
        show_only_unc_t = st.checkbox("미분류만 보기", value=True, key="tax_unc_only")
        t_show = tax_df[tax_df["account_category_id"].isna()] if show_only_unc_t else tax_df
        t_show = t_show.sort_values("issue_date", ascending=False)
        st.caption(f"{len(t_show)}건")
        cat_opts_t = ["(선택 안함)"] + list(cat_name_to_id.keys())
        for _, row in t_show.head(150).iterrows():
            with st.container(border=True):
                rc1, rc2, rc3 = st.columns([3.2, 1.4, 1.2])
                d = row["issue_date"].strftime("%Y-%m-%d") if pd.notna(row["issue_date"]) else "-"
                flag = " · ❌취소" if row["canceled"] else (" · 🔗cash_event 매칭됨" if row["matched_cash_event_id"] else " · ⚠️미매칭")
                rc1.markdown(f"**{row['buyer_name']}** · {d} · ₩{row['total_amount']:,.0f}{flag}")
                cur_idx = cat_opts_t.index(row["cat_name"]) if row["cat_name"] in cat_opts_t else 0
                chosen = rc2.selectbox("계정과목", options=cat_opts_t, index=cur_idx, key=f"taxcat_{row['approval_no']}", label_visibility="collapsed")
                if rc3.button("저장", key=f"taxassign_{row['approval_no']}"):
                    new_val = cat_name_to_id.get(chosen) if chosen != "(선택 안함)" else None
                    SUPA.table("tax_invoices").update({"account_category_id": new_val}).eq("approval_no", row["approval_no"]).execute()
                    st.success("저장 완료"); refresh()

    # ── 송금일정 (cash_events) ────────────────────────────
    with tab_ev:
        show_only_unc_e = st.checkbox("미분류만 보기", value=True, key="ev_unc_only")
        e_show = ev_df[ev_df["account_category_id"].isna()] if show_only_unc_e else ev_df
        e_show = e_show.sort_values("due_date", ascending=False)
        st.caption(f"{len(e_show)}건 · ⚠️ 금액/날짜를 여기서 수정하면 송금캘린더에도 즉시 반영됩니다 (같은 테이블) — 신중하게 사용하세요.")
        cat_opts_e = ["(선택 안함)"] + list(cat_name_to_id.keys())
        for _, row in e_show.head(150).iterrows():
            with st.container(border=True):
                rc1, rc2, rc3 = st.columns([3.2, 1.4, 1.2])
                d = row["due_date"].strftime("%Y-%m-%d") if pd.notna(row["due_date"]) else "-"
                rc1.markdown(f"**{row['brand']}** · {d} · {'입금' if row['direction']=='in' else '지출'} · ₩{row['amount']:,.0f}\n\n{row['title'] or ''}")
                cur_idx = cat_opts_e.index(row["cat_name"]) if row["cat_name"] in cat_opts_e else 0
                chosen = rc2.selectbox("계정과목", options=cat_opts_e, index=cur_idx, key=f"evcat_{row['id']}", label_visibility="collapsed")
                if rc3.button("저장", key=f"evassign_{row['id']}"):
                    new_val = cat_name_to_id.get(chosen) if chosen != "(선택 안함)" else None
                    SUPA.table("cash_events").update({"account_category_id": new_val}).eq("id", row["id"]).execute()
                    st.success("저장 완료"); refresh()

                with st.expander("🔧 금액 / 날짜 직접 수정"):
                    ec1, ec2, ec3 = st.columns([2, 2, 1])
                    new_amount = ec1.number_input(
                        "금액", value=float(row["amount"]), step=10000.0, format="%.0f",
                        key=f"evamt_{row['id']}",
                    )
                    default_date = row["due_date"].date() if pd.notna(row["due_date"]) else date.today()
                    new_due = ec2.date_input("날짜", value=default_date, key=f"evdate_{row['id']}")
                    if ec3.button("수정 저장", key=f"evedit_{row['id']}"):
                        SUPA.table("cash_events").update({
                            "amount": new_amount, "due_date": new_due.isoformat(),
                        }).eq("id", row["id"]).execute()
                        st.success("수정 완료 — 송금캘린더에도 즉시 반영됩니다"); refresh()


# ════════════════════════════════════════════════════════════
# 🤖 AI 계정과목 추천
# ════════════════════════════════════════════════════════════
elif menu == "🤖 AI 계정과목 추천":
    st.subheader("🤖 AI 계정과목 추천")
    st.caption("미분류 은행거래 내역을 Claude가 분석해서 계정과목을 추천합니다. 과거에 직접 분류해둔 사례도 함께 참고해서 추천해요. 검토 후 원하는 항목만 선택해서 일괄 등록하세요.")

    unclassified_bank = bank_df_clean[bank_df_clean["account_category_id"].isna()].sort_values("txn_date", ascending=False) if not bank_df.empty else pd.DataFrame()

    if unclassified_bank.empty:
        st.success("미분류 은행거래가 없습니다 👍")
        st.stop()

    BATCH_SIZE = 20
    st.info(f"미분류 은행거래 {len(unclassified_bank)}건 중 최대 {BATCH_SIZE}건을 한 번에 분석합니다. (한 번에 더 많이 하면 응답이 잘릴 수 있어 배치를 나눴습니다)")
    batch = unclassified_bank.head(BATCH_SIZE)

    def build_fewshot_examples(bdf, cats_by_id, max_examples=25):
        """이미 계정과목이 지정된(=사람이 직접 분류했거나 확정한) 은행거래를 몇 건 뽑아
        AI에게 '우리 회사는 실제로 이렇게 분류한다'는 사례로 함께 보여준다."""
        done = bdf[bdf["account_category_id"].notna()].copy()
        if done.empty:
            return []
        done = done.sort_values("txn_date", ascending=False).head(max_examples)
        examples = []
        for _, r in done.iterrows():
            cat = cats_by_id.get(r["account_category_id"])
            if not cat:
                continue
            examples.append({
                "description": r["description"] or "", "direction": r["direction"],
                "amount": float(r["amount"]), "assigned_code": cat["code"],
            })
        return examples

    def call_claude_suggest(rows, cats, examples=None, notes=None):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None, "ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다."
        cat_list_text = "\n".join(
            f"- {c['code']} ({c['name']}, 유형:{TYPE_LABELS.get(c['type'], c['type'])}): {c.get('description') or ''}"
            for c in cats if c["is_active"]
        )
        examples_text = ""
        if examples:
            ex_lines = "\n".join(
                f"- [{e['direction']}] {e['amount']:,.0f}원 · \"{e['description']}\" → {e['assigned_code']}"
                for e in examples
            )
            examples_text = (
                "\n\n참고: 아래는 이 회사가 과거에 실제로 직접 분류해둔 사례들이다. "
                "적요(문구) 패턴이 비슷한 새 거래가 있으면 최대한 이 사례들과 같은 계정과목으로 맞춰라 (특히 인물/거래처 이름이 겹치면 같은 분류일 가능성이 매우 높다):\n" + ex_lines
            )
        notes_text = notes_as_prompt_text(notes)
        txn_list = [
            {"id": r["id"], "direction": r["direction"], "amount": float(r["amount"]),
             "date": r["txn_date"].strftime("%Y-%m-%d") if pd.notna(r["txn_date"]) else None,
             "description": r["description"] or ""}
            for _, r in rows.iterrows()
        ]
        system = (
            "너는 한국 마케팅 대행사(인플루언서 마케팅)의 은행거래 내역을 보고 가장 알맞은 계정과목을 추천하는 회계 보조원이다. "
            "아래 계정과목 목록 중에서만 골라야 한다. 은행 적요(description)에 외국인 이름이나 개인 이름이 있고 direction이 'out'이면 "
            "대부분 인플루언서 리워드/지급비(COGS_INFLUENCER)일 가능성이 높다. direction이 'in'이고 회사명이 적요에 있으면 매출 계열일 가능성이 높다. "
            "각 거래에 대해 confidence(high/medium/low)와 아주 짧은 reason(15자 이내, 한 문장이 아니라 키워드 수준)을 반드시 포함해라. "
            "출력은 오직 JSON 배열만: [{\"id\":\"...\", \"suggested_code\":\"...\", \"confidence\":\"high|medium|low\", \"reason\":\"...\"}]. "
            "설명 문장, 코드블록(```), 그 외 어떤 텍스트도 절대 포함하지 마라. JSON 배열 하나만 출력해라.\n\n계정과목 목록:\n" + cat_list_text + examples_text + notes_text
        )
        try:
            res = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-5", "max_tokens": 8000, "system": system,
                    "messages": [{"role": "user", "content": f"거래 목록:\n{json.dumps(txn_list, ensure_ascii=False)}"}],
                },
                timeout=90,
            )
            if res.status_code >= 300:
                return None, f"{res.status_code} {res.text[:300]}"
            data = res.json()
            stop_reason = data.get("stop_reason")
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            text = text.strip()
            if text.startswith("```"):
                text = text.strip("`")
                if text.startswith("json"):
                    text = text[4:]
            try:
                items = json.loads(text)
            except json.JSONDecodeError:
                # 응답이 중간에 잘린 경우: 마지막으로 완전히 닫힌 객체까지만 복구
                last_close = text.rfind("},")
                if last_close == -1:
                    last_close = text.rfind("}")
                if last_close == -1:
                    raise
                salvage = text[: last_close + 1].rstrip(",") + "]"
                if not salvage.lstrip().startswith("["):
                    salvage = "[" + salvage
                items = json.loads(salvage)
            if stop_reason == "max_tokens" or len(items) < len(rows):
                st.warning(f"⚠️ 응답이 도중에 잘려서 {len(items)}/{len(rows)}건만 추천됐어요. 나머지는 'AI 추천 실행'을 다시 눌러 이어서 처리하세요.")
            return items, None
        except Exception as e:
            return None, str(e)

    if st.button("🤖 AI 추천 실행", type="primary"):
        with st.spinner("Claude가 거래 내역을 분석 중입니다..."):
            fewshot_examples = build_fewshot_examples(bank_df_clean, cat_by_id)
            biz_notes = load_business_notes()
            items, err = call_claude_suggest(batch, categories, fewshot_examples, biz_notes)
        if err:
            st.error(f"AI 추천 실패: {err}")
        else:
            st.session_state["ai_suggestions"] = {it["id"]: it for it in items if it.get("id")}
            st.success(f"{len(items)}건 추천 완료. 아래에서 확인 후 일괄 등록하세요.")

    suggestions = st.session_state.get("ai_suggestions", {})
    if suggestions:
        code_to_name = {c["code"]: c["name"] for c in categories}
        rows_for_edit = []
        for _, r in batch.iterrows():
            sug = suggestions.get(r["id"])
            if not sug:
                continue
            rows_for_edit.append({
                "id": r["id"],
                "적용": sug.get("confidence") == "high",
                "날짜": r["txn_date"].strftime("%Y-%m-%d") if pd.notna(r["txn_date"]) else "-",
                "적요": r["description"] or "",
                "방향": "입금" if r["direction"] == "in" else "출금",
                "금액": float(r["amount"]),
                "추천 계정과목": code_to_name.get(sug.get("suggested_code"), sug.get("suggested_code")),
                "확신도": sug.get("confidence"),
                "근거": sug.get("reason"),
            })
        edit_df = pd.DataFrame(rows_for_edit)
        st.caption("체크된 항목만 '선택 항목 일괄 등록'으로 반영됩니다. 추천이 틀렸으면 직접 계정과목을 바꾸세요.")
        edited = st.data_editor(
            edit_df,
            column_config={
                "id": None,
                "적용": st.column_config.CheckboxColumn(),
                "추천 계정과목": st.column_config.SelectboxColumn(options=list(cat_name_to_id.keys())),
                "금액": st.column_config.NumberColumn(format="₩%d"),
            },
            column_order=["적용", "날짜", "적요", "방향", "금액", "추천 계정과목", "확신도", "근거"],
            hide_index=True, use_container_width=True, key="ai_edit_table",
        )

        if st.button("✅ 선택 항목 일괄 등록", type="primary"):
            applied = 0
            for i, row in edited.iterrows():
                if row["적용"] and row["추천 계정과목"] in cat_name_to_id:
                    txn_id = rows_for_edit[i]["id"]
                    SUPA.table("bank_transactions").update(
                        {"account_category_id": cat_name_to_id[row["추천 계정과목"]]}
                    ).eq("id", txn_id).execute()
                    applied += 1
            st.session_state.pop("ai_suggestions", None)
            st.success(f"{applied}건 일괄 등록 완료")
            refresh()




elif menu == "🏦 뱅크다 연동":
    st.subheader("🏦 뱅크다 연동")
    st.caption("은행 API 서비스 '뱅크다'를 통해 계좌 거래내역을 자동으로 가져옵니다. 가져온 거래는 기존 계좌 거래내역과 동일한 규칙(dedup_hash)으로 중복 없이 저장되고, 20만원 미만 지출은 자동으로 '단순경비'로 분류됩니다.")

    BANKDA_DEFAULT_URL = "https://a.bankda.com/dtsvc/bank_tr.php"
    bankda_key = os.environ.get("BANKDA_API_KEY")
    bankda_base = os.environ.get("BANKDA_BASE_URL") or BANKDA_DEFAULT_URL

    status_cols = st.columns(2)
    status_cols[0].metric("BANKDA_API_KEY", "설정됨 ✅" if bankda_key else "미설정 ❌")
    status_cols[1].metric("엔드포인트", bankda_base)

    if not bankda_key:
        st.warning(
            "Railway 환경변수에 BANKDA_API_KEY가 아직 없습니다. 뱅크다 콘솔의 ACCESS TOKEN 값을 "
            "Railway 환경변수로 등록해주세요. (BANKDA_BASE_URL은 생략하면 기본 엔드포인트를 씁니다.)"
        )
        st.stop()

    # ── 최근 동기화 결과 (버튼 누른 뒤 바로 여기서 확인) ──────
    recent_logs = SUPA.table("bankda_sync_log").select("*").order("called_at", desc=True).limit(5).execute().data
    if recent_logs:
        st.markdown("**📋 최근 동기화 결과**")
        for lg in recent_logs:
            ok = not lg.get("error_code")
            when = lg["called_at"][:16].replace("T", " ")
            period = f"{lg.get('datefrom','?')}~{lg.get('dateto','?')}"
            if ok:
                st.success(f"{when} · 조회기간 {period} · 조회 {lg.get('record_count',0)}건 · 신규저장 {lg.get('new_count',0)}건")
            else:
                st.error(f"{when} · 조회기간 {period} · 오류[{lg.get('error_code')}]: {lg.get('error_message')}")
        st.caption("결과가 '조회 O건 · 신규저장 0건'이면 실패가 아니라, 이미 저장된 거래라 중복을 걸러낸 것입니다. 실제 데이터는 '📊 대시보드' · '🔗 전체 매칭 현황' 탭에서 확인하세요.")
        st.divider()

    # ── 계좌 라벨 매핑 관리 ──────────────────────────────────
    # 뱅크다가 돌려주는 accountnum마다, 기존에 수동 업로드에서 쓰던 계좌 이름(account_label)을
    # 매핑해둬야 대시보드 표시·dedup이 기존 데이터와 어긋나지 않는다.
    label_rows = SUPA.table("bankda_account_labels").select("*").execute().data
    label_map = {r["accountnum"]: r["account_label"] for r in label_rows}

    with st.expander(f"⚙️ 계좌 라벨 매핑 ({len(label_rows)}건 등록됨)"):
        st.caption(
            "뱅크다 계좌번호별로 화면에 표시할 이름을 정해두세요. 처음 보는 계좌번호는 은행명으로 자동 등록되고, 여기서 이름을 바꿀 수 있습니다. "
            "⚠️ 같은 은행에 계좌가 여러 개 있으면(예: SC제일은행 7705/2490) 기본값(은행명)이 서로 겹칠 수 있으니, 반드시 계좌번호 끝자리를 붙여 "
            "'SC제일은행(7705)' / 'SC제일은행(2490)' 식으로 구분해주세요 — 이름이 같으면 서로 다른 계좌인데도 하나로 취급되어 중복탐지가 정상 거래를 잘못 지울 수 있습니다."
        )
        if label_rows:
            lbl_df = pd.DataFrame(label_rows)[["accountnum", "account_label", "bank_name"]]
            edited_lbl = st.data_editor(
                lbl_df, hide_index=True, use_container_width=True, key="bankda_label_editor",
                column_config={"accountnum": st.column_config.TextColumn("계좌번호", disabled=True),
                                "bank_name": st.column_config.TextColumn("은행명(참고)", disabled=True)},
            )
            if st.button("💾 라벨 저장", key="save_bankda_labels"):
                for _, row in edited_lbl.iterrows():
                    SUPA.table("bankda_account_labels").update(
                        {"account_label": row["account_label"]}
                    ).eq("accountnum", row["accountnum"]).execute()
                st.toast("라벨 저장됨 ✓"); refresh()
        else:
            st.caption("아직 등록된 계좌가 없습니다 — 아래에서 한 번 조회하면 자동으로 추가됩니다.")

    # ── 5분 요청 제한 안내 ───────────────────────────────────
    last_log = SUPA.table("bankda_sync_log").select("called_at,error_code").order("called_at", desc=True).limit(1).execute().data
    if last_log:
        last_dt = datetime.fromisoformat(last_log[0]["called_at"].replace("Z", "+00:00"))
        elapsed_min = (datetime.now(last_dt.tzinfo) - last_dt).total_seconds() / 60
        if elapsed_min < 5:
            st.info(f"⏱️ 마지막 요청 후 {elapsed_min:.1f}분 경과 — 뱅크다는 계좌별로 5분 제한이 있어 바로 재요청하면 실패할 수 있습니다.")

    # ── 조회 조건 ────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns(3)
    date_from = fc1.date_input("조회 시작일", value=date.today() - timedelta(days=3), key="bankda_from")
    date_to = fc2.date_input("조회 종료일", value=date.today(), key="bankda_to")
    is_test = fc3.checkbox("테스트 모드 (istest=y, 기간 무시하고 최근 2건만)", value=False, key="bankda_istest")

    if st.button("🔄 뱅크다에서 거래내역 가져오기", type="primary"):
        form_fields = {
            "datefrom": date_from.strftime("%Y%m%d"),
            "dateto": date_to.strftime("%Y%m%d"),
            "datatype": "json",
            "charset": "utf8",
        }
        if is_test:
            form_fields["istest"] = "y"

        with st.spinner("뱅크다에 거래내역을 요청하는 중입니다..."):
            try:
                res = requests.post(
                    bankda_base,
                    headers={"Authorization": f"Bearer {bankda_key}"},
                    files={k: (None, v) for k, v in form_fields.items()},
                    timeout=30,
                )
                http_status = res.status_code
                payload = res.json()
            except Exception as e:
                SUPA.table("bankda_sync_log").insert({
                    "datefrom": form_fields["datefrom"], "dateto": form_fields["dateto"],
                    "http_status": None, "error_code": "REQUEST_FAILED", "error_message": str(e),
                }).execute()
                st.error(f"요청 실패: {e}")
                st.stop()

        resp = payload.get("response", {})
        description = resp.get("description") or ""
        error_code = resp.get("error_detail_code")

        if description:  # description이 있으면 오류
            SUPA.table("bankda_sync_log").insert({
                "datefrom": form_fields["datefrom"], "dateto": form_fields["dateto"],
                "http_status": http_status, "error_code": error_code, "error_message": description,
                "raw": payload,
            }).execute()
            st.error(f"뱅크다 오류 [{error_code}]: {description}")
            if error_code == "P108":
                st.warning("허용되지 않은 IP입니다 — Railway 배포 서버의 아웃바운드 IP를 뱅크다 콘솔에 등록해야 할 수 있습니다.")
            st.stop()

        bank_rows = resp.get("bank", []) or []

        # ── 계좌 라벨 자동 등록 (처음 보는 계좌번호면 은행명으로 기본 등록) ──
        for r in bank_rows:
            acc = str(r.get("accountnum") or "")
            if acc and acc not in label_map:
                bkname = r.get("bkname") or acc
                SUPA.table("bankda_account_labels").upsert(
                    {"accountnum": acc, "account_label": bkname, "bank_name": bkname},
                    on_conflict="accountnum",
                ).execute()
                label_map[acc] = bkname

        # ── bank_transactions 행으로 변환 + dedup_hash 계산 (수동 업로드와 동일 공식) ──
        rows_to_insert = []
        for r in bank_rows:
            acc = str(r.get("accountnum") or "")
            account_label = label_map.get(acc, r.get("bkname") or acc)
            bkinput = int(r.get("bkinput") or 0)
            bkoutput = int(r.get("bkoutput") or 0)
            direction = "in" if bkinput > 0 else "out"
            amount = bkinput if direction == "in" else bkoutput
            bkdate, bktime = str(r.get("bkdate") or ""), str(r.get("bktime") or "000000")
            txn_date = f"{bkdate[0:4]}-{bkdate[4:6]}-{bkdate[6:8]}" if len(bkdate) == 8 else None
            dt_str = f"{txn_date} {bktime[0:2]}:{bktime[2:4]}:{bktime[4:6]}" if txn_date else None
            desc_parts = [p for p in [r.get("bkjukyo"), r.get("bkcontent")] if p]
            desc = " ".join(desc_parts)
            if r.get("bketc"):
                desc = f"{desc} ({r['bketc']})".strip()
            dedup_hash = hashlib.md5(
                f"{account_label or ''}|{direction}|{amount}|{dt_str or ''}".encode("utf-8")
            ).hexdigest()
            rows_to_insert.append({
                "direction": direction, "amount": amount, "txn_date": txn_date, "txn_datetime": dt_str,
                "description": desc or None, "account_label": account_label,
                "dedup_hash": dedup_hash, "source": "bankda_api",
            })

        actually_added = 0
        if rows_to_insert:
            res2 = SUPA.table("bank_transactions").upsert(
                rows_to_insert, on_conflict="dedup_hash", ignore_duplicates=True
            ).execute()
            actually_added = len(res2.data) if res2.data is not None else len(rows_to_insert)

        SUPA.table("bankda_sync_log").insert({
            "datefrom": form_fields["datefrom"], "dateto": form_fields["dateto"],
            "http_status": http_status, "record_count": len(bank_rows), "new_count": actually_added,
        }).execute()

        skipped = len(rows_to_insert) - actually_added
        msg = f"뱅크다에서 {len(bank_rows)}건 조회 · {actually_added}건 새로 등록됨 ✓"
        if skipped > 0:
            msg += f" · 이미 있던 {skipped}건은 중복이라 건너뜀"
        st.success(msg)
        refresh()

    # ════════════════════════════════════════════════════════
    # 📥 과거 거래내역 일괄 업로드 (뱅크다가 못 가져오는 기간을 은행 파일로 보충)
    # ════════════════════════════════════════════════════════
    st.divider()
    st.markdown("### 📥 과거 거래내역 일괄 업로드")
    st.caption(
        "뱅크다는 최근 데이터만 가져올 수 있어서, 예전 내역은 은행에서 받은 엑셀/CSV 파일을 직접 올려야 합니다. "
        "SC제일은행·기업은행처럼 은행마다 파일 형식이 달라도 자동으로 인식합니다. 위 계좌 라벨과 이름을 맞춰서 올리면 "
        "뱅크다로 가져온 데이터와 한 화면에서 같이 보이고, 20만원 미만 자동분류도 동일하게 적용됩니다."
    )

    STAFF_PAYROLL_NAMES_HIST = ["김선재", "정다영", "양혜준", "구정회", "박솔", "장현우"]
    _COLKEY_HIST = {
        "date": ["거래일시", "거래일자", "거래일", "일자", "날짜", "이용일자", "승인일자"],
        "deposit": ["입금액", "입금", "맡기신금액", "들어온금액"],
        "withdraw": ["출금액", "출금", "찾으신금액", "나간금액", "이용금액"],
        "desc": ["거래내용", "내용", "적요", "거래구분", "받으신분", "보내신분", "가맹점명", "메모", "비고"],
    }

    def _find_col_hist(columns, keys):
        for col in columns:
            c = re.sub(r"\s+", "", str(col).strip())
            for k in keys:
                if k in c:
                    return col
        return None

    def _looks_like_payroll_hist(desc):
        s = re.sub(r"\s+", "", (desc or ""))
        return any(name in s for name in STAFF_PAYROLL_NAMES_HIST)

    def _unescape_x_hist(s):
        if not isinstance(s, str):
            return s
        return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), s)

    # SC제일은행: applyNumberForm 오타 / 기업은행: styleId, xfid 소문자 — 둘 다 styles.xml 패치로 해결
    def _fix_xlsx_style_bug_hist(data: bytes) -> bytes:
        try:
            zin = zipfile.ZipFile(io.BytesIO(data))
            buf = io.BytesIO()
            zout = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
            changed = False
            for item in zin.infolist():
                content = zin.read(item.filename)
                if item.filename == "xl/styles.xml":
                    orig = content
                    content = content.replace(b"applyNumberForm=", b"applyNumberFormat=")
                    content = re.sub(rb'\s+styleId="[^"]*"', b"", content)
                    content = content.replace(b"xfid=", b"xfId=")
                    if content != orig:
                        changed = True
                zout.writestr(item, content)
            zout.close()
            return buf.getvalue() if changed else data
        except Exception:
            return data

    _HEADER_DATE_KW_HIST = ("거래일시", "거래일자", "거래일", "일자", "날짜")
    _HEADER_AMT_KW_HIST = ("찾으신", "맡기신", "출금", "입금")

    def _find_table_header_row_hist(raw_df, max_scan=60):
        for i in range(min(max_scan, len(raw_df))):
            cells = [str(x) for x in raw_df.iloc[i].tolist() if str(x) != "nan"]
            joined = "".join(cells)
            if any(k in joined for k in _HEADER_DATE_KW_HIST) and any(k in joined for k in _HEADER_AMT_KW_HIST):
                return i
        return None

    def _date_ok_hist(v):
        try:
            return pd.notna(pd.to_datetime(str(v)[:19], errors="coerce"))
        except Exception:
            return False

    def _date_count_hist(series):
        return sum(1 for v in series.dropna() if _date_ok_hist(v))

    def _numeric_count_hist(series):
        n = 0
        for v in series.dropna():
            try:
                float(str(v).replace(",", "")); n += 1
            except Exception:
                pass
        return n

    def _hangul_total_hist(series):
        return sum(len(re.findall(r"[가-힣]", str(v))) for v in series.dropna())

    def _guess_headerless_columns_hist(raw_df):
        cols = list(raw_df.columns)
        if not cols:
            return None
        dcounts = {c: _date_count_hist(raw_df[c]) for c in cols}
        c_date = max(dcounts, key=dcounts.get)
        if dcounts[c_date] < 3:
            return None
        numeric_cols = [c for c in cols if c != c_date and _numeric_count_hist(raw_df[c]) >= max(3, len(raw_df) // 4)]
        numeric_cols = sorted(numeric_cols, key=lambda c: cols.index(c))
        if len(numeric_cols) < 2:
            return None
        c_wd, c_dep = numeric_cols[0], numeric_cols[1]
        text_cols = [c for c in cols if c not in (c_date, c_wd, c_dep)]
        text_cols_scored = sorted(text_cols, key=lambda c: -_hangul_total_hist(raw_df[c]))
        c_desc = text_cols_scored[0] if text_cols_scored else None
        return c_date, c_wd, c_dep, c_desc

    def _extract_rows_from_sheet_hist(df, c_date, c_wd, c_dep, c_desc):
        out = []
        for _, r in df.iterrows():
            dv = r.get(c_date)
            if not _date_ok_hist(dv):
                continue
            wd, dep = 0.0, 0.0
            try:
                wd = float(str(r.get(c_wd, 0)).replace(",", "")) if c_wd is not None else 0.0
            except Exception:
                pass
            try:
                dep = float(str(r.get(c_dep, 0)).replace(",", "")) if c_dep is not None else 0.0
            except Exception:
                pass
            if wd == 0 and dep == 0:
                continue
            direction, amount = ("out", int(abs(wd))) if wd > dep else ("in", int(abs(dep)))
            d = pd.to_datetime(str(dv)[:19], errors="coerce")
            due = d.date().isoformat() if pd.notna(d) else None
            norm_dt = d.strftime("%Y-%m-%d %H:%M:%S") if pd.notna(d) else None
            desc = _unescape_x_hist(str(r.get(c_desc, "")).strip()) if c_desc is not None else ""
            raw_dt = str(dv).strip()
            out.append({"direction": direction, "amount": amount, "due_date": due, "desc": desc, "raw_dt": raw_dt, "norm_dt": norm_dt})
        return out

    _ACCOUNT_NO_RE_HIST = re.compile(r"\d{2,4}-\d{1,3}-\d{4,8}")

    def _detect_account_no_hist(raw_df, max_scan=30):
        for i in range(min(max_scan, len(raw_df))):
            for cell in raw_df.iloc[i].tolist():
                s = str(cell)
                if "계좌" in s or "account" in s.lower():
                    m = _ACCOUNT_NO_RE_HIST.search(s)
                    if m:
                        return m.group()
            row_joined = " ".join(str(c) for c in raw_df.iloc[i].tolist())
            if "계좌" in row_joined:
                m = _ACCOUNT_NO_RE_HIST.search(row_joined)
                if m:
                    return m.group()
        return None

    def read_and_parse_bank_file_hist(uploaded_file):
        name = uploaded_file.name.lower()
        all_rows, sheet_info, detected_account_no = [], [], None
        if name.endswith(".xlsx"):
            data = _fix_xlsx_style_bug_hist(uploaded_file.getvalue())
            xls = pd.ExcelFile(io.BytesIO(data))
            for sheetname in xls.sheet_names:
                raw = pd.read_excel(xls, sheet_name=sheetname, header=None)
                if detected_account_no is None:
                    detected_account_no = _detect_account_no_hist(raw)
                hidx = _find_table_header_row_hist(raw)
                if hidx is not None:
                    header = [str(x).strip() if str(x) != "nan" else f"col_{i}" for i, x in enumerate(raw.iloc[hidx].tolist())]
                    df = raw.iloc[hidx + 1:].copy()
                    df.columns = header
                    c_date = _find_col_hist(df.columns, _COLKEY_HIST["date"])
                    c_dep = _find_col_hist(df.columns, _COLKEY_HIST["deposit"])
                    c_wd = _find_col_hist(df.columns, _COLKEY_HIST["withdraw"])
                    c_desc = _find_col_hist(df.columns, _COLKEY_HIST["desc"])
                    if c_date is None or (c_dep is None and c_wd is None):
                        guess = _guess_headerless_columns_hist(df)
                        if guess:
                            c_date, c_wd, c_dep, c_desc = guess
                else:
                    guess = _guess_headerless_columns_hist(raw)
                    if not guess:
                        continue
                    c_date, c_wd, c_dep, c_desc = guess
                    df = raw
                if c_date is None or (c_wd is None and c_dep is None):
                    continue
                rows = _extract_rows_from_sheet_hist(df, c_date, c_wd, c_dep, c_desc)
                if rows:
                    all_rows.extend(rows)
                    sheet_info.append(f"{sheetname}({len(rows)}건)")
        else:
            raw = pd.read_csv(io.BytesIO(uploaded_file.getvalue()), encoding="utf-8-sig", header=None)
            detected_account_no = _detect_account_no_hist(raw)
            hidx = _find_table_header_row_hist(raw)
            if hidx is not None:
                header = [str(x).strip() if str(x) != "nan" else f"col_{i}" for i, x in enumerate(raw.iloc[hidx].tolist())]
                df = raw.iloc[hidx + 1:].copy(); df.columns = header
            else:
                df = raw.copy(); df.columns = [str(c).strip() for c in raw.iloc[0].tolist()]; df = df.iloc[1:]
            c_date = _find_col_hist(df.columns, _COLKEY_HIST["date"])
            c_dep = _find_col_hist(df.columns, _COLKEY_HIST["deposit"])
            c_wd = _find_col_hist(df.columns, _COLKEY_HIST["withdraw"])
            c_desc = _find_col_hist(df.columns, _COLKEY_HIST["desc"])
            if c_date is not None and (c_dep is not None or c_wd is not None):
                all_rows = _extract_rows_from_sheet_hist(df, c_date, c_wd, c_dep, c_desc)
                sheet_info = [f"CSV({len(all_rows)}건)"]

        if not all_rows:
            return None, "거래 데이터를 찾지 못했습니다. 파일 형식을 확인해주세요.", None
        return all_rows, " · ".join(sheet_info), detected_account_no

    def compute_dedup_hash_hist(account_label, direction, amount, dt_str):
        raw = f"{account_label or ''}|{direction}|{amount}|{dt_str or ''}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    hist_up = st.file_uploader("과거 계좌 거래내역 (.csv / .xlsx)", type=["csv", "xlsx"], key="hist_bank_up")
    if hist_up is not None:
        try:
            parsed, info_or_err, detected_acc = read_and_parse_bank_file_hist(hist_up)
        except Exception as ex:
            parsed, info_or_err, detected_acc = None, f"파일을 읽지 못했습니다: {ex}", None

        if parsed is None:
            st.error(info_or_err)
        else:
            existing_accounts = sorted(set(label_map.values()) | {t.get("account_label") for t in bank_txns if t.get("account_label")})
            default_label = None
            if detected_acc:
                matches = [a for a in existing_accounts if detected_acc in a]
                default_label = matches[0] if matches else None
                st.info(f"📎 파일에서 계좌번호를 찾았어요: **{detected_acc}**" +
                        (f" — 기존 '{default_label}'과 같은 계좌로 보여요." if default_label else " — 새 계좌인 것 같아요, 이름을 확인해주세요."))
            acc_opts = existing_accounts + ["+ 새 계좌 이름 직접 입력"]
            default_index = acc_opts.index(default_label) if default_label in acc_opts else len(acc_opts) - 1
            acc_pick = st.selectbox("이 파일은 어느 계좌 것인가요?", acc_opts, index=default_index, key="hist_acc_pick")
            account_label_hist = (
                st.text_input(
                    "계좌 이름 입력", value=(detected_acc or ""),
                    placeholder="예: SC제일은행(7705), SC제일은행(2490), 기업은행",
                    key="hist_acc_text",
                    help="⚠️ 같은 은행에 계좌가 여러 개면(예: SC제일은행 7705/2490) 반드시 계좌번호 끝자리까지 구분해서 이름을 붙여주세요 — 이름이 같으면 서로 다른 계좌인데도 중복탐지가 하나의 계좌로 착각해 정상 거래를 잘못 지울 수 있습니다.",
                )
                if acc_pick == "+ 새 계좌 이름 직접 입력" else acc_pick
            )

            st.caption(f"시트별 인식 현황: {info_or_err}")
            if not parsed:
                st.warning("입금/출금 금액이 있는 행을 찾지 못했습니다.")
            elif not account_label_hist.strip():
                st.warning("계좌 이름을 입력해주세요.")
            else:
                existing_hashes_hist = {t.get("dedup_hash") for t in bank_txns if t.get("dedup_hash")}
                for r in parsed:
                    h = compute_dedup_hash_hist(account_label_hist.strip(), r["direction"], r["amount"], r.get("norm_dt") or r.get("due_date"))
                    r["_hash"] = h
                    r["_dup"] = h in existing_hashes_hist
                    r["_payroll"] = _looks_like_payroll_hist(r["desc"])

                n_total = len(parsed)
                n_payroll = sum(1 for r in parsed if r["_payroll"])
                n_dup = sum(1 for r in parsed if r["_dup"] and not r["_payroll"])
                n_target = n_total - n_payroll

                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("인식된 거래", f"{n_total:,}건")
                sc2.metric("급여 제외", f"{n_payroll:,}건")
                sc3.metric("중복 예상(자동 skip)", f"{n_dup:,}건")
                sc4.metric("등록 시도", f"{n_target:,}건")

                st.caption(f"**{account_label_hist.strip()}** · 아래는 확인용 샘플 15건입니다 (날짜·금액·적요가 제대로 읽혔는지만 확인하세요).")
                sample_df = pd.DataFrame([{
                    "날짜": r["due_date"] or "-", "구분": "받을" if r["direction"] == "in" else "나갈",
                    "금액": f"₩{r['amount']:,.0f}", "적요": r["desc"],
                    "비고": "급여제외" if r["_payroll"] else ("중복예상" if r["_dup"] else ""),
                } for r in parsed[:15]])
                st.dataframe(sample_df, hide_index=True, use_container_width=True)

                if st.button(f"✅ 급여 제외하고 {n_target:,}건 한 번에 등록", type="primary", key="hist_confirm_btn", disabled=n_target == 0):
                    rows_to_insert_hist = [{
                        "direction": r["direction"], "amount": r["amount"],
                        "txn_date": r["due_date"], "txn_datetime": r.get("norm_dt"),
                        "description": r["desc"] or None, "account_label": account_label_hist.strip(),
                        "dedup_hash": r["_hash"], "source": "manual_upload",
                    } for r in parsed if not r["_payroll"]]
                    actually_added_hist = 0
                    if rows_to_insert_hist:
                        res_hist = SUPA.table("bank_transactions").upsert(
                            rows_to_insert_hist, on_conflict="dedup_hash", ignore_duplicates=True
                        ).execute()
                        actually_added_hist = len(res_hist.data) if res_hist.data is not None else len(rows_to_insert_hist)
                    skipped_hist = len(rows_to_insert_hist) - actually_added_hist
                    msg = f"{actually_added_hist}건 새로 등록됨 ✓"
                    if skipped_hist > 0:
                        msg += f" · 이미 있던 {skipped_hist}건은 중복이라 건너뜀"
                    st.success(msg)
                    refresh()


# ════════════════════════════════════════════════════════════
# ⚙️ 계정과목 설정
# ════════════════════════════════════════════════════════════
elif menu == "⚙️ 계정과목 설정":
    st.subheader("⚙️ 계정과목 설정")
    st.caption("계정과목을 추가/수정하거나 비활성화할 수 있습니다. 표에서 직접 수정 후 '변경사항 저장'을 눌러주세요. (삭제 대신 '사용' 체크를 해제하는 걸 권장 — 이미 연결된 거래가 있으면 완전 삭제는 실패할 수 있습니다)")

    cat_df_full = pd.DataFrame(categories)[["id", "code", "name", "type", "description", "sort_order", "is_active"]]
    edited_cats = st.data_editor(
        cat_df_full,
        num_rows="dynamic",
        column_config={
            "id": None,
            "type": st.column_config.SelectboxColumn(options=SETTINGS_TYPE_OPTIONS),
            "is_active": st.column_config.CheckboxColumn("사용"),
            "sort_order": st.column_config.NumberColumn(),
        },
        column_order=["code", "name", "type", "description", "sort_order", "is_active"],
        hide_index=True, use_container_width=True, key="cat_settings_editor",
    )

    if st.button("💾 변경사항 저장", type="primary"):
        orig_by_id = {c["id"]: c for c in categories}
        saved, failed = 0, 0
        for _, row in edited_cats.iterrows():
            payload = {
                "code": row["code"], "name": row["name"], "type": row["type"],
                "description": row["description"] if pd.notna(row["description"]) else None,
                "sort_order": int(row["sort_order"]) if pd.notna(row["sort_order"]) else 0,
                "is_active": bool(row["is_active"]),
            }
            row_id = row.get("id")
            try:
                if pd.isna(row_id) or not row_id:
                    SUPA.table("fin_account_categories").insert(payload).execute()
                    saved += 1
                else:
                    orig = orig_by_id.get(row_id)
                    if orig and any(orig.get(k) != payload[k] for k in payload):
                        SUPA.table("fin_account_categories").update(payload).eq("id", row_id).execute()
                        saved += 1
            except Exception as e:
                failed += 1
                st.error(f"'{row.get('code')}' 저장 실패: {e}")
        st.success(f"{saved}건 저장 완료" + (f" ({failed}건 실패)" if failed else ""))
        refresh()


# ════════════════════════════════════════════════════════════
# 📄 손익계산서 (실시간)
# ════════════════════════════════════════════════════════════
elif menu == "📄 손익계산서":
    st.subheader("📄 실시간 손익계산서 (은행거래 기준, 현금주의)")
    st.caption("부가세 예수금/대급금은 손익이 아닌 재무상태표 항목이라 이 표에서 제외했습니다. 법인세는 발생분이 있을 때만 표시됩니다.")

    p1, p2 = st.columns(2)
    start_d = p1.date_input("시작일", value=date(today.year, today.month, 1))
    end_d = p2.date_input("종료일", value=date.today())

    if bank_df.empty:
        st.info("데이터 없음"); st.stop()

    mask = (bank_df_clean["txn_date"] >= pd.Timestamp(start_d)) & (bank_df_clean["txn_date"] <= pd.Timestamp(end_d))
    pl_df = bank_df_clean[mask]
    pl = compute_pl(pl_df)
    rev_items, cogs_items, combined_sga = pl["rev_items"], pl["cogs_items"], pl["combined_sga"]
    total_rev, total_cogs, gross_profit = pl["total_rev"], pl["total_cogs"], pl["gross_profit"]
    total_sga, op_profit = pl["total_sga"], pl["op_profit"]
    other_income, other_expense = pl["other_income"], pl["other_expense"]
    pretax, corp_tax, net_profit = pl["pretax"], pl["corp_tax"], pl["net_profit"]

    def render_section(title, items, total, color="#1F3864"):
        st.markdown(f"<span style='color:{color};font-weight:700'>{title}</span>", unsafe_allow_html=True)
        if items:
            rows = pd.DataFrame([{"계정과목": k, "금액": f"₩{v:,.0f}"} for k, v in items.items() if v != 0])
            st.dataframe(rows, hide_index=True, use_container_width=True)
        st.markdown(f"**합계: ₩{total:,.0f}**")
        st.markdown("")

    render_section("Ⅰ. 매출액", rev_items, total_rev)
    render_section("Ⅱ. 매출원가", cogs_items, total_cogs)
    st.markdown(f"### Ⅲ. 매출총이익 : ₩{gross_profit:,.0f}")
    st.markdown("")
    render_section("Ⅳ. 판매비와관리비", combined_sga, total_sga)
    st.markdown(f"### Ⅴ. 영업손익 : ₩{op_profit:,.0f}")
    st.markdown("")
    st.markdown(f"**Ⅵ. 영업외수익** : ₩{other_income:,.0f}")
    st.markdown(f"**Ⅶ. 영업외비용** : ₩{other_expense:,.0f}")
    st.markdown(f"### Ⅷ. 법인세차감전손익 : ₩{pretax:,.0f}")
    st.markdown(f"**Ⅸ. 법인세등** : ₩{corp_tax:,.0f}")
    st.markdown(f"## Ⅹ. 당기순손익 : ₩{net_profit:,.0f}")

    uncategorized_in_period = pl_df[pl_df["account_category_id"].isna()]
    if not uncategorized_in_period.empty:
        st.warning(f"⚠️ 이 기간 내 미분류 은행거래 {len(uncategorized_in_period)}건(₩{uncategorized_in_period['amount'].sum():,.0f})은 위 손익계산서에서 빠져 있습니다 — '전체 매칭 현황' 또는 'AI 계정과목 추천'에서 분류해주세요.")


# ════════════════════════════════════════════════════════════
# 📝 CFO 인사이트
# ════════════════════════════════════════════════════════════
elif menu == "📝 CFO 인사이트":
    st.subheader("📝 CFO 인사이트")
    st.caption("숫자만 나열하는 손익계산서 대신, Claude가 CFO 관점에서 이번 기간에 무슨 일이 있었고 왜 그런지, 리스크와 다음 액션까지 스토리로 풀어드립니다.")

    # ── 패턴/맥락 메모 관리 (여기서 바로 관리, AI 추천 탭에서도 항상 참고됨) ──
    with st.expander("🧠 기억해둘 패턴/맥락 메모 관리", expanded=False):
        st.caption("계정과목 규칙, 매출·매입 연관성, 계절성, 주요 고객사 특징 등을 짧은 메모로 남겨두세요. AI 계정과목 추천과 이 CFO 인사이트 둘 다 항상 참고합니다.")
        notes_all = SUPA.table("fin_business_notes").select("*").order("created_at", desc=True).execute().data
        notes_df = pd.DataFrame(notes_all) if notes_all else pd.DataFrame(columns=["id", "note", "tag", "is_active"])
        if "id" not in notes_df.columns:
            notes_df["id"] = None
        edited_notes = st.data_editor(
            notes_df[["id", "note", "tag", "is_active"]] if not notes_df.empty else pd.DataFrame(columns=["id", "note", "tag", "is_active"]),
            num_rows="dynamic",
            column_config={
                "id": None,
                "note": st.column_config.TextColumn("메모", width="large"),
                "tag": st.column_config.TextColumn("태그(선택)", help="예: 계정과목규칙 / 계절성 / 고객사 / 리스크"),
                "is_active": st.column_config.CheckboxColumn("사용"),
            },
            column_order=["note", "tag", "is_active"],
            hide_index=True, use_container_width=True, key="notes_editor",
        )
        if st.button("💾 메모 저장", key="save_notes"):
            orig_by_id = {n["id"]: n for n in notes_all}
            saved = 0
            for _, row in edited_notes.iterrows():
                if not str(row.get("note") or "").strip():
                    continue
                payload = {
                    "note": row["note"], "tag": row.get("tag") if pd.notna(row.get("tag")) else None,
                    "is_active": bool(row["is_active"]) if pd.notna(row.get("is_active")) else True,
                }
                row_id = row.get("id")
                if pd.isna(row_id) or not row_id:
                    SUPA.table("fin_business_notes").insert(payload).execute(); saved += 1
                else:
                    orig = orig_by_id.get(row_id)
                    if orig and any(orig.get(k) != payload[k] for k in payload):
                        SUPA.table("fin_business_notes").update(payload).eq("id", row_id).execute(); saved += 1
            st.success(f"{saved}건 저장 완료"); load_business_notes.clear(); st.rerun()

    st.divider()

    # ── 기간 선택 (이번 기간 vs 직전 동일길이 기간 자동 비교) ──
    p1, p2 = st.columns(2)
    ci_start = p1.date_input("분석 시작일", value=date(today.year, today.month, 1), key="cfo_start")
    ci_end = p2.date_input("분석 종료일", value=date.today(), key="cfo_end")

    if bank_df.empty:
        st.info("데이터 없음"); st.stop()

    period_len = (pd.Timestamp(ci_end) - pd.Timestamp(ci_start)).days + 1
    prev_end = pd.Timestamp(ci_start) - pd.Timedelta(days=1)
    prev_start = prev_end - pd.Timedelta(days=period_len - 1)

    cur_mask = (bank_df_clean["txn_date"] >= pd.Timestamp(ci_start)) & (bank_df_clean["txn_date"] <= pd.Timestamp(ci_end))
    prev_mask = (bank_df_clean["txn_date"] >= prev_start) & (bank_df_clean["txn_date"] <= prev_end)
    pl_cur = compute_pl(bank_df_clean[cur_mask])
    pl_prev = compute_pl(bank_df_clean[prev_mask])

    m1, m2, m3 = st.columns(3)
    m1.metric("이번 기간 매출", f"₩{pl_cur['total_rev']:,.0f}", delta=f"₩{pl_cur['total_rev']-pl_prev['total_rev']:,.0f}")
    m2.metric("이번 기간 영업이익", f"₩{pl_cur['op_profit']:,.0f}", delta=f"₩{pl_cur['op_profit']-pl_prev['op_profit']:,.0f}")
    m3.metric("이번 기간 당기순손익", f"₩{pl_cur['net_profit']:,.0f}", delta=f"₩{pl_cur['net_profit']-pl_prev['net_profit']:,.0f}")

    def call_claude_cfo_insight(pl_cur, pl_prev, period_text, prev_period_text, notes):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None, "ANTHROPIC_API_KEY 환경변수가 설정되어 있지 않습니다."
        notes_text = notes_as_prompt_text(notes)
        system = (
            "너는 한국의 인플루언서 마케팅 대행사(브랜드슬램)를 위해 일하는 CFO다. "
            "아래 손익 데이터를 바탕으로, 대표에게 보고하는 CFO 코멘터리를 한국어로 작성해라. "
            "말투는 사업계획서/이사회 보고서에 어울리는 신중하고 분석적인 톤이되, 딱딱한 보고서가 아니라 스토리로 풀어써라. "
            "다음 구조를 따르되 마크다운 헤더(###)로 구분해라: "
            "1) 이번 기간 한 줄 요약, 2) 전기간 대비 무엇이 바뀌었고 왜 그런지 (반드시 숫자를 인용), "
            "3) 눈여겨봐야 할 리스크나 이상 신호, 4) 다음 기간에 대표가 취하면 좋을 구체적 액션 2~3가지. "
            "확정적으로 단정하지 말고, 데이터가 시사하는 바를 짚어주는 정도로 신중하게 서술해라. "
            "재무 지식이 부족한 대표도 이해할 수 있게 쉬운 말로 써라." + notes_text
        )
        user_content = (
            f"[이번 기간: {period_text}]\n{json.dumps(pl_cur, ensure_ascii=False)}\n\n"
            f"[직전 비교 기간: {prev_period_text}]\n{json.dumps(pl_prev, ensure_ascii=False)}"
        )
        try:
            res = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-sonnet-5", "max_tokens": 3000, "system": system,
                      "messages": [{"role": "user", "content": user_content}]},
                timeout=90,
            )
            if res.status_code >= 300:
                return None, f"{res.status_code} {res.text[:300]}"
            data = res.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return text.strip(), None
        except Exception as e:
            return None, str(e)

    if st.button("📝 CFO 인사이트 생성", type="primary"):
        with st.spinner("Claude가 재무 스토리를 작성하는 중입니다..."):
            notes = load_business_notes()
            narrative, err = call_claude_cfo_insight(
                pl_cur, pl_prev,
                f"{ci_start} ~ {ci_end}", f"{prev_start.date()} ~ {prev_end.date()}",
                notes,
            )
        if err:
            st.error(f"생성 실패: {err}")
        else:
            st.session_state["cfo_narrative"] = narrative
            SUPA.table("fin_cfo_narratives").insert({
                "period_start": str(ci_start), "period_end": str(ci_end), "content": narrative,
            }).execute()
            st.success("생성 완료 (자동 저장됨)")

    if st.session_state.get("cfo_narrative"):
        st.markdown("---")
        st.markdown(st.session_state["cfo_narrative"])

    st.divider()
    with st.expander("🗂️ 지난 CFO 인사이트 기록 보기"):
        past = SUPA.table("fin_cfo_narratives").select("*").order("created_at", desc=True).limit(10).execute().data
        if not past:
            st.caption("저장된 기록이 없습니다.")
        for p in past:
            with st.container(border=True):
                st.caption(f"{p['period_start']} ~ {p['period_end']} · 생성일 {p['created_at'][:16].replace('T',' ')}")
                st.markdown(p["content"])
