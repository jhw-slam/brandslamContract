"""재무캘린더 첫 화면 '🏠 자동 정리' 화면. 계산은 auto_ledger.py, 설계는 docs/finance_auto_ledger_plan.md.

원칙(대표 10/27): 승인 없이도 숫자는 처음부터 100% 보인다 / 상세보기에서 바로 고치고 한 번에 저장 / 상쇄는 자동 제외 / 미수금·캠페인 매칭은 표시하지 않음.
선택하는 동안에는 아무것도 저장하지 않는다 — 저장 버튼을 눌렀을 때만 DB(bank_transactions.account_category_id)에 쓴다.
"""
import pandas as pd
import streamlit as st

from auto_ledger import backtest, by_category, offset_pairs, summarize
from expense_breakdown import normalize_merchant

TYPE_ORDER = ["revenue", "cost_cogs", "cost_sga", "labor", "tax", "other", "balance_sheet", "wait"]
TYPE_LABEL = {"revenue": "매출", "cost_cogs": "매출원가", "cost_sga": "판관비", "labor": "인건비", "tax": "세금", "other": "영업외/기타",
              "balance_sheet": "재무상태표 항목", "wait": "분류 대기"}
KEEP = "(그대로)"
PAGE_SIZES = [20, 50, 100]


def won(v):
    return f"₩{v:,.0f}"


def _period_bounds(label, today):
    if label == "전체":
        return None, None
    if label == "이번달":
        s = today.replace(day=1)
        return s, s + pd.offsets.MonthEnd(0)
    if label == "지난달":
        e = today.replace(day=1) - pd.Timedelta(days=1)
        return e.replace(day=1), e
    if label == "이번 분기":
        q = (today.month - 1) // 3
        s = pd.Timestamp(today.year, q * 3 + 1, 1)
        return s, s + pd.offsets.MonthEnd(3)
    return pd.Timestamp(today.year, 1, 1), pd.Timestamp(today.year, 12, 31)    # 올해


def _in_period(df, s, e):
    if s is None:
        return df
    return df[(df["txn_date"] >= s) & (df["txn_date"] <= e)]


def _chunks(seq, n=100):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def save_categories(SUPA, groups):
    """groups: {분류id: [거래id,...]}. 대표님이 직접 정한 분류 = 확정(is_auto_categorized=False). 저장한 건수를 돌려준다."""
    n = 0
    for cid, ids in groups.items():
        for part in _chunks(list(ids)):
            SUPA.table("bank_transactions").update({"account_category_id": cid, "is_auto_categorized": False}).in_("id", part).execute()
            n += len(part)
    return n


def _save_offsets(SUPA, ids, decision):
    rows = [{"bank_transaction_id": i, "decision": decision, "decided_by": "장현우"} for i in ids]
    SUPA.table("bank_offset_overrides").upsert(rows, on_conflict="bank_transaction_id").execute()


def render_home(SUPA, ledger, categories, refresh, today=None):
    """ledger: auto_ledger.classify_ledger 결과. refresh(): 캐시를 비우고 화면을 다시 그린다."""
    today = today or pd.Timestamp.today().normalize()
    ver = st.session_state.get("ledger_ver", 0)
    st.subheader("🏠 자동 정리")
    st.caption("은행에 들어온 입출금 **전부**를 시스템이 먼저 정리해 보여줘요. 확인이 덜 된 것도 숫자에는 처음부터 다 들어 있고, "
               "특이한 것만 아래 '상세보기'에서 고치면 돼요. 상쇄(결제↔취소·환불)는 자동으로 재무에서 빠져 있어요.")
    if st.session_state.get("ledger_flash"):
        st.success(st.session_state.pop("ledger_flash"))
    if ledger.empty:
        st.info("아직 은행 거래가 없어요.")
        return

    label = st.radio("기간", ["이번달", "지난달", "이번 분기", "올해", "전체"], index=3, horizontal=True, key="ledger_period")
    s, e = _period_bounds(label, today)
    d = _in_period(ledger, s, e)
    sm = summarize(d)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("매출", won(sm["revenue"]))
    c2.metric("비용(원가·판관비·인건비)", won(sm["cost"]))
    c3.metric("영업이익", won(sm["op_profit"]), help="분류된 거래만 기준. 분류 대기 거래는 아래 별도 줄로 보여줘요.")
    c4.metric("순현금흐름", won(sm["net_cash"]), help="내부이체·상쇄를 뺀 모든 입금 − 출금(분류 대기 포함)")
    tp = sm["trust_pct"]
    st.progress(min(tp["확정"] + tp["자동"], 1.0), text=f"✓ 대표님 확정 {tp['확정'] * 100:.0f}% · ≈ 자동 분류 {tp['자동'] * 100:.0f}% · ? 분류 대기 {tp['대기'] * 100:.0f}%  (금액 기준, {sm['n']:,}건)")
    if sm["wait_in"] or sm["wait_out"]:
        st.caption(f"? 분류 대기 — 입금 {sm['wait_n_in']:,}건 {won(sm['wait_in'])} · 출금 {sm['wait_n_out']:,}건 {won(sm['wait_out'])} "
                   "(위 매출·비용에는 안 들어가고, 순현금흐름에는 들어가 있어요)")
    st.caption("ℹ️ 이 화면은 새 자동 정리 기준이에요. 대시보드·손익계산서는 아직 예전 방식(DB에 저장된 분류 기준)이라 숫자가 다를 수 있어요 — 차례로 이 기준에 맞춰요.")
    st.caption(f"♻️ 상쇄로 자동 제외된 거래 {sm['offset_n']:,}건 (약 {won(sm['offset_amount'])}씩 쌍) — 재무 숫자에 반영하지 않았어요.")

    # ── 계정과목별 표 ──
    cat = by_category(d)
    if cat.empty:
        st.info("이 기간에는 정리할 거래가 없어요.")
    else:
        def shown(r):
            if r["type"] == "revenue" or r["type"] in ("other", "balance_sheet"):
                return r["in"] - r["out"]
            if r["type"] == "wait":
                return r["in"] or r["out"]
            return r["out"] - r["in"]
        cat["금액"] = cat.apply(shown, axis=1)
        cat["_o"] = cat["type"].map(lambda t: TYPE_ORDER.index(t) if t in TYPE_ORDER else 99)
        cat = cat.sort_values(["_o", "금액"], ascending=[True, False])
        view = pd.DataFrame({"구분": cat["type"].map(TYPE_LABEL), "계정과목": cat["name"], "건수": cat["n"], "금액": cat["금액"].map(won),
                             "✓확정": cat["n_confirmed"], "≈자동": cat["n_auto"], "?대기": cat["n_wait"]})
        st.dataframe(view, hide_index=True, use_container_width=True)
        st.caption("금액: 매출은 입금−출금, 비용·세금은 출금−입금. 급여·인건비는 개인별 내역 없이 합계만 보여줘요.")

    # ── 상세보기 ──
    st.markdown("#### 🔍 상세보기 — 계정과목을 고르면 거래가 나와요")
    opts = {}
    for _, r in (cat.iterrows() if not cat.empty else []):
        opts[f"{TYPE_LABEL.get(r['type'], r['type'])} · {r['name']} · {r['n']:,}건"] = (r["type"], r["name"])
    pick = st.selectbox("상세보기", ["(선택 안 함)"] + list(opts.keys()), key="ledger_pick", label_visibility="collapsed")
    if pick != "(선택 안 함)":
        _render_detail(SUPA, d, categories, opts[pick], ver, refresh)

    _render_offsets(SUPA, d, ver, refresh)
    _render_quality(ledger, categories)


def _render_detail(SUPA, d, categories, key, ver, refresh):
    typ, name = key
    live = d[~d["offset_excluded"] & (d["ledger_type"] != "transfer")]
    if typ == "wait":
        rows = live[(live["ledger_state"] == "대기") & (live["direction"] == ("in" if "입금" in name else "out"))]
    else:
        rows = live[(live["ledger_cat_name"] == name) & (live["ledger_type"] == typ) & (live["ledger_state"] != "대기")]
    active = [c for c in categories if c.get("is_active", True)]
    name_to_id = {c["name"]: c["id"] for c in active}
    cat_opts = [KEEP] + [c["name"] for c in active]
    st.markdown(f"**{name}** — {len(rows):,}건 · {won(rows['amount'].sum())}")

    if typ == "labor":
        st.info("급여·인건비는 개인별 내역을 보여주지 않아요. 월 합계만 볼 수 있어요. (분류가 맞으면 아래 '현재 분류 그대로 확정'만 누르세요)")
        m = rows.assign(월=rows["txn_date"].dt.strftime("%Y-%m")).groupby("월")["amount"].sum().reset_index()
        m["금액"] = m["amount"].map(won)
        st.dataframe(m[["월", "금액"]], hide_index=True, use_container_width=True)
    if typ != "wait":
        auto_rows = rows[(rows["ledger_state"] == "자동") & rows["ledger_cat_id"].notna()]
        if len(auto_rows):
            with st.form(f"ledger_confirm_{ver}_{typ}_{name}"):
                st.caption(f"자동 분류된 {len(auto_rows):,}건({won(auto_rows['amount'].sum())})이 맞다면 한 번에 확정해요. 확정하면 '대표님 확정'으로 저장돼요(아직 저장 전이에요).")
                go_all = st.form_submit_button(f"✅ 자동 분류 {len(auto_rows):,}건을 현재 분류 그대로 확정", type="primary")
            if go_all:
                _do_save(SUPA, {cid: list(g["id"]) for cid, g in auto_rows.groupby("ledger_cat_id")}, "그대로 확정", refresh)
    if typ == "labor":
        return

    show_conf = st.checkbox("대표님이 이미 확정한 거래도 보기", value=(typ == "wait" and False), key=f"ledger_showconf_{ver}")
    todo = rows if show_conf else rows[rows["ledger_state"] != "확정"]
    if todo.empty:
        st.success("확인할 거래가 없어요 — 모두 확정돼 있어요.")
        return
    st.caption(f"확인이 필요한 {len(todo):,}건을 보여줘요. 바꿀 줄만 '새 분류'를 고르고 맨 아래 **저장**을 한 번 누르세요. 고르는 동안은 저장되지 않아요.")
    t1, t2 = st.tabs(["거래처별 일괄(빠름)", "거래별"])

    with t1:
        g = todo.assign(_m=todo["description"].map(normalize_merchant)).groupby("_m")
        mg = pd.DataFrame({"거래처": [k for k, _ in g], "건수": [len(x) for _, x in g], "합계": [x["amount"].sum() for _, x in g],
                           "현재": [", ".join(sorted({str(v) for v in x["ledger_cat_name"].fillna("(분류 대기)")})) for _, x in g],
                           "근거": [(x["ledger_why"].iloc[0] or "")[:40] for _, x in g], "_ids": [list(x["id"]) for _, x in g]})
        mg = mg.sort_values("합계", ascending=False).reset_index(drop=True)
        size = st.selectbox("한 번에 보기", PAGE_SIZES, key=f"ledger_msize_{ver}")
        pg = int(st.number_input("페이지", min_value=1, max_value=max(1, -(-len(mg) // size)), value=1, step=1, key=f"ledger_mpage_{ver}")) if len(mg) > size else 1
        part = mg.iloc[(pg - 1) * size: pg * size]
        ed_df = pd.DataFrame({"거래처": part["거래처"], "건수": part["건수"], "합계": part["합계"].map(won), "현재": part["현재"], "근거": part["근거"], "새 분류": KEEP})
        ed_df.index = part.index
        with st.form(f"ledger_mform_{ver}_{typ}_{name}_{pg}"):
            edm = st.data_editor(ed_df, hide_index=True, use_container_width=True, key=f"ledger_med_{ver}_{typ}_{name}_{pg}",
                                 disabled=["거래처", "건수", "합계", "현재", "근거"],
                                 column_config={"새 분류": st.column_config.SelectboxColumn("새 분류 ▼ (이 거래처 전체)", options=cat_opts, required=True, width="medium")})
            gom = st.form_submit_button("💾 고른 거래처 저장 (여기서 저장돼요)", type="primary")
        if gom:
            groups = {}
            for ix in edm.index:
                nm = edm.loc[ix, "새 분류"]
                if nm != KEEP:
                    groups.setdefault(name_to_id[nm], []).extend(mg.loc[ix, "_ids"])
            if not groups:
                st.info("바뀐 항목이 없어요. '새 분류'를 고른 뒤 저장해주세요.")
            else:
                _do_save(SUPA, groups, "거래처별 분류", refresh)

    with t2:
        td = todo.sort_values("txn_date", ascending=False)
        size2 = st.selectbox("한 번에 보기", PAGE_SIZES, key=f"ledger_tsize_{ver}")
        pg2 = int(st.number_input("페이지", min_value=1, max_value=max(1, -(-len(td) // size2)), value=1, step=1, key=f"ledger_tpage_{ver}")) if len(td) > size2 else 1
        v = td.iloc[(pg2 - 1) * size2: pg2 * size2]
        ed2 = pd.DataFrame({"날짜": v["txn_date"].dt.strftime("%Y-%m-%d"), "구분": v["direction"].map({"in": "입금", "out": "출금"}), "금액": v["amount"].map(won),
                            "적요": v["description"].fillna(""), "현재": v["ledger_cat_name"].fillna("(분류 대기)") + " · " + v["ledger_state"],
                            "근거": v["ledger_why"].fillna("").str.slice(0, 40), "새 분류": KEEP})
        ed2.index = v["id"].tolist()
        with st.form(f"ledger_tform_{ver}_{typ}_{name}_{pg2}"):
            edt = st.data_editor(ed2, hide_index=True, use_container_width=True, key=f"ledger_ted_{ver}_{typ}_{name}_{pg2}",
                                 disabled=["날짜", "구분", "금액", "적요", "현재", "근거"],
                                 column_config={"새 분류": st.column_config.SelectboxColumn("새 분류 ▼", options=cat_opts, required=True, width="medium")})
            got = st.form_submit_button("💾 고른 내용 저장 (여기서 저장돼요)", type="primary")
        if got:
            groups = {}
            for rid in edt.index:
                nm = edt.loc[rid, "새 분류"]
                if nm != KEEP:
                    groups.setdefault(name_to_id[nm], []).append(rid)
            if not groups:
                st.info("바뀐 항목이 없어요. '새 분류'를 고른 뒤 저장해주세요.")
            else:
                _do_save(SUPA, groups, "거래별 분류", refresh)


def _do_save(SUPA, groups, what, refresh):
    try:
        n = save_categories(SUPA, groups)
    except Exception as e:
        st.error(f"❌ 저장하지 못했어요 ({type(e).__name__}: {str(e)[:160]}) — 일부만 저장됐을 수 있으니 새로고침해서 확인해주세요.")
        return
    st.session_state["ledger_ver"] = st.session_state.get("ledger_ver", 0) + 1
    st.session_state["ledger_flash"] = f"✅ {what}: {n:,}건을 저장했어요(대표님 확정)."
    refresh()


def _render_offsets(SUPA, d, ver, refresh):
    pairs = offset_pairs(d)
    n_ex = int(pairs["excluded"].sum()) if not pairs.empty else 0
    n_su = int((~pairs["excluded"]).sum()) if not pairs.empty else 0
    amt_ex = float(pairs.loc[pairs["excluded"], "amount"].sum()) if n_ex else 0.0
    amt_su = float(pairs.loc[~pairs["excluded"], "amount"].sum()) if n_su else 0.0
    with st.expander(f"♻️ 상쇄 보관함 — 자동 제외 {n_ex:,}쌍 {won(amt_ex)} (재무 미반영) · 의심 {n_su:,}쌍 {won(amt_su)} (재무 포함)", expanded=False):
        st.caption("같은 계좌·같은 금액의 입금↔출금(결제↔취소·환불)이에요. **자동 제외** 쌍은 재무에서 이미 빠져 있고 계정과목도 필요 없어요(데이터는 지우지 않고 기록으로 보관). "
                   "**의심** 쌍은 근거가 약해서 숫자에 그대로 들어 있어요 — 상쇄가 맞으면 '상쇄 확정'을 체크하세요.")
        if pairs.empty:
            st.info("상쇄로 보이는 거래가 없어요.")
            return
        tabs = st.tabs([f"자동 제외 {n_ex:,}쌍", f"의심 {n_su:,}쌍"])
        for tab, excluded in zip(tabs, (True, False)):
            with tab:
                p = pairs[pairs["excluded"] == excluded].head(100)
                if p.empty:
                    st.caption("해당하는 쌍이 없어요.")
                    continue
                col = "정상 거래로 되돌리기" if excluded else "상쇄 확정"
                ed = pd.DataFrame({col: False, "구분": p["tier"], "계좌": p["account"], "금액": p["amount"].map(won),
                                   "출금일": p["out_date"].dt.strftime("%Y-%m-%d"), "입금일": p["in_date"].dt.strftime("%Y-%m-%d"),
                                   "간격(일)": p["gap_days"], "적요(출금 / 입금)": p["out_desc"].str.slice(0, 22) + " / " + p["in_desc"].str.slice(0, 22)})
                ed.index = p.index
                st.caption("최근 100쌍까지 보여줘요." if len(pairs[pairs["excluded"] == excluded]) > 100 else "")
                with st.form(f"ledger_off_{ver}_{int(excluded)}"):
                    edo = st.data_editor(ed, hide_index=True, use_container_width=True, key=f"ledger_offed_{ver}_{int(excluded)}", disabled=[c for c in ed.columns if c != col])
                    go = st.form_submit_button("💾 체크한 쌍 저장 (여기서 저장돼요)", type="primary")
                if go:
                    sel = [ix for ix in edo.index if bool(edo.loc[ix, col])]
                    if not sel:
                        st.info("체크한 쌍이 없어요.")
                        continue
                    ids = [i for ix in sel for i in pairs.loc[ix, "ids"]]
                    try:
                        _save_offsets(SUPA, ids, "keep" if excluded else "offset")
                    except Exception as e:
                        msg = str(e)
                        if "check" in msg.lower() or "23514" in msg:
                            st.error("❌ 아직 '상쇄 확정'을 저장할 수 없어요 — `migrations/20261019_bank_offset_confirm.sql`을 DB에 적용해야 해요. (아무것도 저장되지 않았어요)")
                        else:
                            st.error(f"❌ 저장하지 못했어요 ({type(e).__name__}: {msg[:160]}) — 아직 `migrations/20261012_bank_offset_overrides.sql`이 적용 안 됐을 수 있어요.")
                        continue
                    st.session_state["ledger_ver"] = st.session_state.get("ledger_ver", 0) + 1
                    st.session_state["ledger_flash"] = f"✅ {len(sel):,}쌍을 {'정상 거래로 되돌렸어요(재무에 다시 반영)' if excluded else '상쇄로 확정했어요(재무에서 제외)'}."
                    refresh()


def _render_quality(ledger, categories):
    with st.expander("📏 자동 정리 정확도 점검 (대표님이 확정한 거래로 시험한 결과)", expanded=False):
        bt = backtest(ledger, categories)
        src = ledger[~ledger["offset_excluded"]].groupby("ledger_source")["amount"].agg(["count", "sum"]).reset_index()
        if bt["confirmed"]:
            st.markdown(f"- 대표님이 확정한 거래 **{bt['confirmed']:,}건**을 하나씩 가리고 시스템이 맞히게 했어요.\n"
                        f"- **같은 거래처 이력**: {bt['hist_covered']:,}건에 답을 냈고(전체의 {bt['hist_cover_pct'] * 100:.0f}%) 그중 **{bt['hist_acc'] * 100:.0f}%** 가 맞았어요.\n"
                        f"- **키워드 규칙**: {bt['rule_covered']:,}건에 답을 냈고(전체의 {bt['rule_cover_pct'] * 100:.0f}%) 그중 **{bt['rule_acc'] * 100:.0f}%** 가 맞았어요.")
        else:
            st.caption("확정된 거래가 아직 없어서 시험할 수 없어요.")
        if not src.empty:
            st.caption("분류 근거별 건수(상쇄 제외): " + " · ".join(f"{r['ledger_source']} {int(r['count']):,}건" for _, r in src.iterrows()))
        st.caption("규칙 정확도가 낮은 항목(예: PAYPAL은 인플루언서 지급일 수도, 해외 직원 급여일 수도 있어요)은 같은 거래처 이력을 먼저 따라가고, 이력이 갈리면 규칙을 써요.")
