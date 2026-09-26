import os
import json
from datetime import date, datetime

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
    "revenue": "매출", "cost_cogs": "매출원가", "cost_sga": "판관비",
    "labor": "인건비", "tax": "세금", "other": "영업외/기타",
}
TYPE_ORDER = ["revenue", "cost_cogs", "cost_sga", "labor", "tax", "other"]


@st.cache_data(ttl=45)
def load_all():
    categories = SUPA.table("fin_account_categories").select("*").order("sort_order").execute().data
    events = SUPA.table("cash_events").select(
        "id,project_id,direction,category,title,amount,due_date,paid,paid_date,memo,account_category_id"
    ).execute().data
    projects = SUPA.table("projects").select("id,brand,campaign").execute().data
    bank_txns = SUPA.table("bank_transactions").select(
        "id,direction,amount,txn_date,txn_datetime,description,matched_cash_event_id,account_label,account_category_id,created_at"
    ).order("txn_date", desc=True).execute().data
    tax_invs = SUPA.table("tax_invoices").select(
        "approval_no,write_date,issue_date,buyer_biz_no,buyer_name,total_amount,supply_amount,vat,kind,"
        "matched_cash_event_id,canceled,account_category_id"
    ).order("issue_date", desc=True).execute().data
    return categories, events, projects, bank_txns, tax_invs


def refresh():
    load_all.clear()
    st.rerun()


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
    "메뉴", ["📊 대시보드", "🔗 전체 매칭 현황", "🤖 AI 계정과목 추천", "⚙️ 계정과목 설정", "📄 손익계산서"],
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
                d = row["txn_date"].strftime("%Y-%m-%d") if pd.notna(row["txn_date"]) else "-"
                rc1.markdown(f"**{'입금' if row['direction']=='in' else '출금'}** · {d} · ₩{row['amount']:,.0f}\n\n{row['description'] or ''}  {match_info}{sus}")
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

    def call_claude_suggest(rows, cats, examples=None):
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
            "설명 문장, 코드블록(```), 그 외 어떤 텍스트도 절대 포함하지 마라. JSON 배열 하나만 출력해라.\n\n계정과목 목록:\n" + cat_list_text + examples_text
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
            items, err = call_claude_suggest(batch, categories, fewshot_examples)
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
            "type": st.column_config.SelectboxColumn(options=TYPE_ORDER),
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

    def type_net(t, expense_side=True):
        sub = pl_df[pl_df["cat_type"] == t]
        inflow = sub[sub["direction"] == "in"]["amount"].sum()
        outflow = sub[sub["direction"] == "out"]["amount"].sum()
        return (outflow - inflow) if expense_side else (inflow - outflow)

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
    combined_sga = {**sga_items}
    for k, v in labor_items.items():
        combined_sga[k] = combined_sga.get(k, 0) + v
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
