import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 레포 루트의 campaign_margin.py 를 어디서 실행해도 찾게
from campaign_margin import DEFAULT_TARGET_MARGIN, build_campaigns, cash_events, monthly_cashflow, summarize

st.set_page_config(page_title="Campaign Margin", layout="wide")

# ── 앱 공통 비밀번호 게이트 (다른 페이지와 동일. 대표 전용 비밀번호는 따로 걸지 않음) ──
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

TODAY = datetime.now(timezone(timedelta(hours=9))).date()
ICON = {"over": "🔴", "near": "🟠", "ok": "🟢", "wait": "⚪", "short": "🟠", "done": "🟢", "no_target": "⚪"}


def won(n):
    if n is None:
        return "-"
    n = float(n)
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f}억"
    if abs(n) >= 1e4:
        return f"{n / 1e4:,.0f}만원"
    return f"{n:,.0f}원"


def tryq(fn, default):
    try:
        return fn(), None
    except Exception as e:
        return default, f"{type(e).__name__}: {str(e)[:140]}"


# ══════════════════════════════════════════════════════════════
# 데이터 읽기 — 직원이 쌓은 데이터를 가져오기만 한다 (30초 캐시)
# 선택 테이블/칸이 아직 DB에 없어도 화면은 열리고, 없는 부분은 '입력 대기'로 표시한다.
# ══════════════════════════════════════════════════════════════
CAMP_BASE = "id,account_id,campaign_name,open_date,status,contract_url,contract_name,invoice_url,invoice_number,invoice_amount,invoice_currency,invoice_attached_at,brand_approved_at"


@st.cache_data(ttl=30)
def load_all():
    camps, e_camp = tryq(lambda: SUPA.table("sales_campaigns").select(CAMP_BASE + ",invoice_issue_date,invoice_vat_type")
                         .order("open_date", desc=True).execute().data, None)
    missing_vat = camps is None
    if missing_vat:  # 부가세 구분·발행일 칸이 아직 없는 DB
        camps, e_camp = tryq(lambda: SUPA.table("sales_campaigns").select(CAMP_BASE).order("open_date", desc=True).execute().data, [])
    accts, _ = tryq(lambda: SUPA.table("sales_accounts").select("id,brand_name").execute().data, [])
    links, e_links = tryq(lambda: SUPA.table("payment_request_campaigns")
                          .select("payment_request_id,campaign_id,ratio,allocated_amount,currency").limit(5000).execute().data, [])
    # 계좌번호·신분증 등 민감 칸은 가져오지 않는다
    reqs, _ = tryq(lambda: SUPA.table("payment_requests")
                   .select("id,influencer_name,amount,currency,scheduled_date,status,paid_at,content_link,brand_names").limit(5000).execute().data, [])
    deps, e_dep = tryq(lambda: SUPA.table("campaign_deposit_links").select("bank_transaction_id,campaign_id,ratio,allocated_amount")
                       .limit(5000).execute().data, [])
    ids = list({d["bank_transaction_id"] for d in deps})
    banks = []
    if ids:
        banks, _ = tryq(lambda: SUPA.table("bank_transactions").select("id,txn_date,description,amount").in_("id", ids).execute().data, [])
    sets, e_set = tryq(lambda: SUPA.table("campaign_margin_settings").select("*").execute().data, [])
    return {
        "camps": camps, "accts": {a["id"]: a for a in accts}, "links": links, "reqs": {r["id"]: r for r in reqs},
        "deps": deps, "banks": {b["id"]: b for b in banks}, "sets": {s["campaign_id"]: s for s in sets},
        "missing": {"vat": missing_vat, "deposit_links": bool(e_dep), "settings": bool(e_set), "links": bool(e_links)},
        "err": e_camp,
    }


def flash(msg, ok=True):
    st.session_state["cm_flash"] = (msg, ok)


def save_settings(campaign_id, count_text, margin_text):
    try:
        cnt = int(count_text.replace(",", "")) if count_text.strip() else None
        mg = float(margin_text.replace("%", "")) / 100 if margin_text.strip() else None
        if cnt is not None and cnt < 0:
            raise ValueError("목표 개수는 0 이상이어야 해요")
        if mg is not None and not (0 <= mg < 1):
            raise ValueError("목표 마진율은 0~99% 로 입력해주세요")
    except ValueError as e:
        flash(f"입력값을 확인해주세요 ({e})", False)
        return
    try:
        SUPA.table("campaign_margin_settings").upsert(
            {"campaign_id": campaign_id, "target_content_count": cnt, "target_margin": mg, "updated_by": "장현우",
             "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="campaign_id").execute()
        load_all.clear()
        flash("목표를 저장했어요.")
    except Exception as e:
        flash(f"저장하지 못했어요 ({type(e).__name__}: {str(e)[:160]}). migrations/20261012_campaign_margin_settings.sql 이 DB에 적용됐는지 확인해주세요.", False)


# ══════════════════════════════════════════════════════════════
st.title("📈 캠페인 마진율 관리")
st.caption("김선재(캠페인·계약서·인보이스·입금 확인)와 곽재선(콘텐츠 송금)이 쌓은 데이터를 가져와 보여줘요. 이 화면에서 따로 계산해 넣는 값은 없어요.")

if st.session_state.get("cm_flash"):
    _m, _ok = st.session_state.pop("cm_flash")
    (st.success if _ok else st.error)(_m)

data = load_all()
if data["err"]:
    st.error(f"❌ 캠페인 데이터를 읽지 못했어요 ({data['err']})")
    st.stop()

miss = data["missing"]
notes = []
if miss["vat"]:
    notes.append("인보이스 부가세 구분·발행일 칸(sales_campaigns.invoice_vat_type / invoice_issue_date)")
if miss["deposit_links"]:
    notes.append("입금 확인 테이블(campaign_deposit_links)")
if notes:
    st.info("📌 아직 DB에 없는 항목: " + " · ".join(notes) + " — 팀스페이스 레포의 `migrations/20261009_margin_data_collection.sql` 을 DB에 적용하면 "
            "김선재님이 입력한 부가세 구분과 입금 확인이 여기에 나타나요. (그 전에도 화면은 열리고, 해당 칸은 '입력 대기'로 보여요)")
if miss["settings"]:
    st.info("📌 목표 콘텐츠 개수 저장 테이블이 아직 없어요 — `migrations/20261012_campaign_margin_settings.sql` 적용 후 입력할 수 있어요.")

# ── 상단 설정 ────────────────────────────────────────────────
top1, top2, top3 = st.columns([2, 2, 3])
target_pct = top1.number_input("목표 마진율 (%)", min_value=0.0, max_value=99.0, value=DEFAULT_TARGET_MARGIN * 100, step=1.0, format="%.0f")
target = target_pct / 100
top2.metric("원가 한도", f"매출의 {100 - target_pct:.0f}%", "매출 100 중 콘텐츠 총 원가", delta_color="off")
brands = sorted({(data["accts"].get(c.get("account_id")) or {}).get("brand_name") or "" for c in data["camps"]} - {""})
brand_pick = top3.selectbox("브랜드", ["전체"] + brands)

rows_all = build_campaigns(data["camps"], data["accts"], data["links"], data["reqs"], data["deps"], data["banks"], data["sets"], default_target=target)
rows = [r for r in rows_all if brand_pick == "전체" or r["brand"] == brand_pick]
sm = summarize(rows)

if not data["camps"]:
    st.info("아직 등록된 캠페인이 없어요 — 0건. 김선재님이 팀스페이스에서 캠페인을 등록하면 여기에 나타나요.")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("캠페인", f"{sm['n']}개", f"매출 확정 {sm['n_rev']}개", delta_color="off")
k2.metric("매출(인보이스 공급가)", won(sm["revenue"]) if sm["revenue"] else "0원")
k3.metric("콘텐츠 원가(배분)", won(sm["cost"]) if sm["cost"] else "0원", f"지급 {won(sm['cost_paid'])} · 예정 {won(sm['cost_planned'])}", delta_color="off")
k4.metric("마진율", f"{sm['margin_rate'] * 100:.1f}%" if sm["margin_rate"] is not None else "매출 대기",
          f"목표 {target_pct:.0f}% 대비 {(sm['margin_rate'] - target) * 100:+.1f}%p" if sm["margin_rate"] is not None else None, delta_color="off")
k5.metric("콘텐츠 개수", f"{sm['n_contents']}개", f"목표 입력 {sm['n_target_set']}/{sm['n']}개 캠페인 · 목표합 {sm['target_n']}", delta_color="off")
if sm["foreign"]:
    st.warning(f"외화 송금이 연결된 캠페인이 {sm['foreign']}곳 있어요. 환율을 추측하지 않아서 원화 합계에 넣지 않았어요(캠페인 상세에서 확인).")

tab1, tab2, tab3, tab4 = st.tabs(["① 콘텐츠 개수", "② 금액 (입금 대비 사용)", "③ 입금·출금 (현금흐름)", "④ 마진율 목표"])
label_of = lambda r: f"{r['brand'] or '(브랜드 미정)'} · {r['name']}" + (f" ({r['open_date']})" if r["open_date"] else "")

# ── ① 콘텐츠 개수 ───────────────────────────────────────────
with tab1:
    st.caption("애초에 목표한 콘텐츠 개수가 채워졌는지 **개수만** 봐요. 송금 1건 = 콘텐츠 1개로 세고, PPL(한 콘텐츠가 여러 캠페인)은 각 캠페인에 1개씩 올라가요.")
    if rows:
        df = pd.DataFrame([{
            "브랜드": r["brand"], "캠페인": r["name"], "목표": r["target_n"] if r["target_n"] is not None else None, "올라온 개수": r["n_contents"],
            "달성률": (min(r["n_contents"] / r["target_n"], 9.99) * 100 if r["target_n"] else None),
            "상태": f"{ICON[r['count_code']]} {r['count_label']}", "PPL 공유": r["n_shared"]} for r in rows])
        st.dataframe(df, hide_index=True, use_container_width=True, column_config={
            "목표": st.column_config.NumberColumn(format="%d개"), "올라온 개수": st.column_config.NumberColumn(format="%d개"),
            "달성률": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100)})
    st.markdown("**목표 입력/수정**")
    if rows:
        with st.form("cm_settings"):
            f1, f2, f3 = st.columns([4, 2, 2])
            pick = f1.selectbox("캠페인", [r["id"] for r in rows], format_func=lambda i: label_of(next(r for r in rows if r["id"] == i)))
            cur = next(r for r in rows if r["id"] == pick)
            cnt_text = f2.text_input("목표 콘텐츠 개수", value="" if cur["target_n"] is None else str(cur["target_n"]), placeholder="예: 10")
            mg_text = f3.text_input("이 캠페인 목표 마진율 % (선택)", value="", placeholder=f"비우면 {target_pct:.0f}")
            st.caption("저장 버튼을 눌러야 반영돼요. (김선재님이 팀스페이스 캠페인 등록에서 입력하게 되면 같은 값을 그대로 써요)")
            if st.form_submit_button("저장", type="primary", disabled=miss["settings"]):
                save_settings(pick, cnt_text, mg_text)
                st.rerun()

# ── ② 금액 ──────────────────────────────────────────────────
with tab2:
    st.caption("입금(매출) 대비 **사용한 금액**이에요. 한 콘텐츠에 여러 캠페인이 나오면(PPL) 송금액을 캠페인 수로 나눠(또는 정한 비율로) 각 캠페인의 원가에 반영돼요. "
               "'캠페인(브랜드사)' 입장에서만 본 금액이라 회사 전체 지출 합계와는 달라요.")
    if rows:
        df = pd.DataFrame([{
            "브랜드": r["brand"], "캠페인": r["name"],
            "인보이스(공급가)": won(r["revenue"]) if r["revenue"] is not None else ("외화·환산 필요" if r["invoice_amount"] is not None and not r["invoice_krw"] else "인보이스 대기"),
            "입금 확정(공급가)": (won(r["cash_revenue"]) if r["cash_revenue"] is not None else ("확인 필요(부가세)" if r["deposits"] else "입금 확인 대기")),
            "사용 원가": won(r["cost"]), "원가율(인보이스)": (r["cost"] / r["revenue"] * 100 if r["revenue"] else None),
            "원가율(입금)": (r["cost"] / r["cash_revenue"] * 100 if r["cash_revenue"] else None),
            "지급 완료": won(r["cost_paid"]), "지급 예정": won(r["cost_planned"]),
            "부가세": ("확인 필요" if r["vat_unknown"] else (r["invoice_vat_type"].split(" ")[0] if r["invoice_vat_type"] else "-"))} for r in rows])
        st.dataframe(df, hide_index=True, use_container_width=True, column_config={
            "원가율(인보이스)": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100),
            "원가율(입금)": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100)})
        st.caption("매출은 부가세를 뺀 공급가예요(부가세 '포함'이면 ÷1.1). 입금은 공급가+10%로 들어오므로 ÷1.1 해서 비교하고, 부가세 구분을 모르면 계산하지 않고 '확인 필요'로 둬요.")

# ── ③ 입금·출금 ─────────────────────────────────────────────
with tab3:
    st.caption("현금이 움직이는 **시점**만 봐요. 입금 = 김선재님이 확정한 입금(부가세 포함 금액) · 출금 = 콘텐츠 송금의 지급일(아직이면 예정일). "
               "아직 은행 출금과 연결하는 데이터는 없어서, 지급 처리된 날짜를 기준으로 보여줘요.")
    ev = cash_events(rows)
    if not ev:
        st.info("입금·출금 내역이 아직 없어요 — 0건. 입금 확인과 콘텐츠 송금이 쌓이면 시점별로 나타나요.")
    else:
        mc = monthly_cashflow(ev)
        mdf = pd.DataFrame([{"월": m["month"], "입금": m["in"], "출금(지급완료)": m["out_paid"], "출금(예정)": m["out_planned"], "순현금": m["net"], "누적": m["cum"]} for m in mc])
        cc1, cc2 = st.columns([3, 2])
        with cc1:
            st.markdown("**월별 입금 · 출금**")
            st.dataframe(mdf.assign(**{c: mdf[c].map(lambda v: f"₩{v:,.0f}") for c in mdf.columns if c != "월"}), hide_index=True, use_container_width=True)
        with cc2:
            chart = mdf[mdf["월"] != "일정 미정"].set_index("월")[["입금", "출금(지급완료)", "출금(예정)"]]
            if not chart.empty:
                st.bar_chart(chart)
        st.markdown("**날짜순 내역**")
        edf = pd.DataFrame([{"날짜": e["date"].isoformat() if e["date"] else "일정 미정", "구분": ("🔵 입금" if e["kind"] == "입금" else "🔴 출금"),
                             "상태": e["state"], "금액": f"₩{e['amount']:,.0f}", "캠페인": e["campaign"], "내용": e["memo"]} for e in ev])
        st.dataframe(edf, hide_index=True, use_container_width=True)

# ── ④ 마진율 목표 ───────────────────────────────────────────
with tab4:
    st.caption(f"목표 마진율 {target_pct:.0f}% = 매출 100일 때 콘텐츠 총 원가는 {100 - target_pct:.0f}%(원가 한도)예요. "
               "🔴 원가 초과(과잉) · 🟠 한도 90% 이상(근접) · 🟢 여유 · ⚪ 매출(인보이스) 대기. 위험한 캠페인부터 보여줘요.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("🔴 원가 초과", f"{sm['n_over']}개")
    c2.metric("🟠 한도 근접", f"{sm['n_near']}개")
    c3.metric("🟢 여유", f"{sum(1 for r in rows if r['m_code'] == 'ok')}개")
    c4.metric("⚪ 매출 대기", f"{sm['n_wait']}개")
    only_bad = st.checkbox("문제(🔴·🟠)만 보기", key="cm_only_bad")
    order = {"over": 0, "near": 1, "ok": 2, "wait": 3}
    shown = sorted([r for r in rows if not only_bad or r["m_code"] in ("over", "near")], key=lambda r: (order[r["m_code"]], -(r["usage"] or 0)))
    if not shown:
        st.info("해당하는 캠페인이 없어요 — 0건")
    else:
        df = pd.DataFrame([{
            "신호": f"{ICON[r['m_code']]} {r['m_label']}", "브랜드": r["brand"], "캠페인": r["name"],
            "매출(공급가)": won(r["revenue"]) if r["revenue"] is not None else "-",
            "원가 한도": won(r["limit"]) if r["limit"] is not None else "-", "사용 원가": won(r["cost"]),
            "한도 사용률": (r["usage"] * 100 if r["usage"] is not None else None),
            "남은 한도": (won(r["remaining_limit"]) if r["remaining_limit"] is not None else "-"),
            "마진율": (f"{r['margin_rate'] * 100:.1f}%" if r["margin_rate"] is not None else "-"),
            "목표 대비": (f"{(r['margin_rate'] - r['target_margin']) * 100:+.1f}%p" if r["margin_rate"] is not None else "-"),
            "남은 콘텐츠": ("-" if r["remaining_n"] is None else f"{r['remaining_n']}개"),
            "콘텐츠당 쓸 수 있는 원가": (won(r["per_content_budget"]) if r["per_content_budget"] is not None else "-")} for r in shown])
        st.dataframe(df, hide_index=True, use_container_width=True, column_config={
            "한도 사용률": st.column_config.ProgressColumn(format="%.0f%%", min_value=0, max_value=100)})
        st.caption("'콘텐츠당 쓸 수 있는 원가' = 남은 원가 한도 ÷ 남은 콘텐츠 개수(목표 − 올라온 개수). 목표 개수를 입력하면 계산돼요.")

# ── 캠페인 상세 (입금부터 계약·인보이스·콘텐츠까지) ──────────────
st.markdown("### 🔎 캠페인 상세")
if rows:
    sel = st.selectbox("캠페인 선택", [r["id"] for r in rows], format_func=lambda i: label_of(next(r for r in rows if r["id"] == i)), key="cm_detail")
    r = next(x for x in rows if x["id"] == sel)
    d1, d2, d3 = st.columns(3)
    with d1:
        st.markdown("**계약**")
        st.write("계약서 첨부: " + ("✅ 있음" if r["contract"] else "❌ 없음 (입력 대기)"))
        if r["contract_url"]:
            st.markdown(f"[계약서 열기]({r['contract_url']})")
        st.write(f"상태: {r['status'] or '-'} · 오픈 {r['open_date'] or '-'}")
    with d2:
        st.markdown("**인보이스**")
        if r["invoice_amount"] is None:
            st.write("인보이스 금액: 입력 대기")
        else:
            st.write(f"번호 {r['invoice_no'] or '-'} · 금액 {r['invoice_amount']:,.0f} {r['invoice_currency']}")
            st.write(f"부가세: {r['invoice_vat_type'] or '⚠️ 확인 필요'} · 발행일 {r['invoice_issue_date'] or '-'}")
            if r["revenue"] is not None:
                st.write(f"공급가(매출): {r['revenue']:,.0f}원")
        if r["invoice_url"]:
            st.markdown(f"[인보이스 열기]({r['invoice_url']})")
    with d3:
        st.markdown("**입금 확정**")
        if not r["deposits"]:
            st.write("입금 확인 대기 — 0건")
        for dp in r["deposits"]:
            st.write(f"{dp['date'] or '-'} · ₩{dp['gross']:,.0f} · {dp['who']}")
    st.markdown(f"**연결된 콘텐츠 {r['n_contents']}개**" + (f" (PPL 공유 {r['n_shared']}개)" if r["n_shared"] else ""))
    if r["contents"]:
        cdf = pd.DataFrame([{"인플루언서": c["who"], "이 캠페인 배분액": f"{c['allocated']:,.0f} {c['currency']}", "PPL": (f"{c['n_campaigns']}개 캠페인 공유" if c["shared"] else "-"),
                             "상태": ("지급완료 " + str(c["paid_date"] or "")) if c["paid"] else ("예정 " + str(c["scheduled"] or "일정 미정")), "콘텐츠 링크": c["link"]} for c in r["contents"]])
        st.dataframe(cdf, hide_index=True, use_container_width=True, column_config={"콘텐츠 링크": st.column_config.LinkColumn()})
    else:
        st.caption("연결된 콘텐츠(송금)가 아직 없어요 — 곽재선님이 팀스페이스에서 송금 등록할 때 이 캠페인을 선택하면 쌓여요.")

st.caption(f"기준일 {TODAY.isoformat()} (한국 시간) · 30초마다 DB에서 다시 읽어요 · 값이 없으면 0 또는 '대기'로 표시하고 가짜 숫자는 만들지 않아요.")
