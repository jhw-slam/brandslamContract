import os
from datetime import date

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="인플루언서 인보이스", layout="wide")

BRANDSLAM = {"name": "주식회사 브랜드슬램", "biz": "284-88-03016", "ceo": "장현우",
             "addr": "서울시 강남구 테헤란로 7길 11, 한덕빌딩 9층 902호"}

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

@st.cache_resource
def infdb():
    url = os.environ.get("INFLU_SUPABASE_URL"); key = os.environ.get("INFLU_SUPABASE_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

won = lambda n: "₩{:,}".format(int(n or 0))

def projects_map():
    projs = SUPA.table("projects").select("id,brand,product,company_id").order("created_at").execute().data
    comp = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    opts = {}
    for p in projs:
        opts[f"{comp.get(p['company_id'],'')} · {p.get('product') or p['brand']}"] = (p["id"], comp.get(p["company_id"], p["brand"]))
    return opts

def search_influencers(q):
    db = infdb()
    if not db or not q:
        return []
    try:
        return db.table("influencer_master").select(
            "influencer_id,name,platform,instagram_followers,account_url").or_(
            f"influencer_id.ilike.*{q}*,name.ilike.*{q}*").limit(20).execute().data
    except Exception:
        return []

def avg_views_of(infid):
    db = infdb()
    if not db or not infid:
        return 0
    try:
        rows = db.table("koc_contents").select("play_count").eq("influencer_id", infid).limit(60).execute().data
        vals = [r["play_count"] for r in rows if r.get("play_count")]
        return int(sum(vals) / len(vals)) if vals else 0
    except Exception:
        return 0

def round_unit(x, unit):
    unit = max(int(unit), 1)
    return int(round(x / unit) * unit)

def suggest_price(followers, views, cpv, cpf, base, unit):
    """업체당 단가 제안 = base + 평균조회수×조회수단가 + 팔로워×팔로워단가"""
    return round_unit(base + (views or 0) * cpv + (followers or 0) * cpf, unit)

def items_of(pid):
    return SUPA.table("inf_invoice_items").select("*").eq("project_id", pid).order("created_at").execute().data

# ── 화면 ──────────────────────────────────────────────────────
st.title("인플루언서 인보이스")
st.caption("팔로워·평균조회수로 업체당 단가(제안가)를 산출하고, PPL 수를 곱해 청구금액을 만듭니다. "
           "실제비용은 따로 입력해 마진율을 원하는 값(15/20%…)으로 맞출 수 있어요.")

opts = projects_map()
if not opts:
    st.warning("프로젝트가 없습니다. '계약 콘솔'에서 먼저 계약(프로젝트)을 추가하세요."); st.stop()
plabel = st.selectbox("프로젝트 선택", list(opts.keys()))
pid, brand_name = opts[plabel]

if infdb() is None:
    st.info("ℹ️ 인플루언서 자동검색을 쓰려면 Railway에 INFLU_SUPABASE_URL / INFLU_SUPABASE_KEY (DB_confidential 프로젝트)를 추가하세요. 지금은 수동 등록으로도 됩니다.")

# ── 산출(제안가) 공식 설정 ────────────────────────────────────
with st.expander("⚙️ 업체당 단가(제안가) 공식 설정"):
    f = st.columns(4)
    cpv = f[0].number_input("조회수 단가(원/view)", min_value=0, value=20, step=5)
    cpf = f[1].number_input("팔로워 단가(원/follower)", min_value=0, value=10, step=1)
    base = f[2].number_input("기본가(base)", min_value=0, value=0, step=100000)
    unit = f[3].number_input("반올림 단위(원)", min_value=1, value=100000, step=10000)
    st.caption("예) 팔로워 26만 × 10원 = 260만 + 조회수분 → 반올림 단위로 정리되어 '업체당 단가' 제안값이 됩니다. (수정 가능)")

# ── 인플루언서 등록 ───────────────────────────────────────────
st.subheader("인플루언서 등록")
tab_auto, tab_manual = st.tabs(["🔎 ID/이름 검색 등록", "✍️ 수동 등록"])

with tab_auto:
    q = st.text_input("인플루언서 ID 또는 이름", placeholder="예: lifewatara")
    results = search_influencers(q) if q else []
    if q and not results:
        st.caption("검색 결과 없음 (또는 인플루언서 DB 미연결).")
    for r in results:
        fol = r.get("instagram_followers") or 0
        c = st.columns([4, 2, 2, 1.4])
        c[0].write(f"**{r.get('name') or r['influencer_id']}** · `{r['influencer_id']}`")
        c[1].write(f"{r.get('platform') or '-'}")
        c[2].write(f"팔로워 {fol:,}")
        if c[3].button("등록", key="reg" + r["influencer_id"]):
            av = avg_views_of(r["influencer_id"])
            per = suggest_price(fol, av, cpv, cpf, base, unit)
            SUPA.table("inf_invoice_items").insert({
                "project_id": pid, "influencer_id": r["influencer_id"], "name": r.get("name"),
                "platform": r.get("platform"), "followers": fol, "avg_views": av,
                "per_brand_price": per, "ppl_count": 1, "calc_price": per, "actual_cost": 0,
                "account_doc_url": r.get("account_url")}).execute()
            st.toast(f"{r.get('name')} 등록 · 업체당 단가 {won(per)} (평균조회수 {av:,})"); st.rerun()

with tab_manual:
    with st.form("manual_add", clear_on_submit=True):
        mc = st.columns(5)
        mname = mc[0].text_input("이름/핸들")
        mfol = mc[1].number_input("팔로워", min_value=0, step=1000)
        mviews = mc[2].number_input("평균조회수", min_value=0, step=1000)
        mppl = mc[3].number_input("PPL 수", min_value=1, value=1, step=1)
        mactual = mc[4].number_input("실제비용(총)", min_value=0, step=100000)
        if st.form_submit_button("등록") and mname:
            per = suggest_price(mfol, mviews, cpv, cpf, base, unit)
            SUPA.table("inf_invoice_items").insert({
                "project_id": pid, "influencer_id": None, "name": mname, "platform": "manual",
                "followers": int(mfol), "avg_views": int(mviews), "per_brand_price": per,
                "ppl_count": int(mppl), "calc_price": per * int(mppl), "actual_cost": int(mactual)}).execute()
            st.toast(f"{mname} 등록 · 업체당 {won(per)} × {mppl} = {won(per*mppl)}"); st.rerun()

# ── 비용 산출표 ───────────────────────────────────────────────
items = items_of(pid)
st.subheader("비용 산출표")
if not items:
    st.info("아직 등록된 인플루언서가 없습니다. 위에서 등록하세요."); st.stop()

def calc_of(it):
    return int((it.get("per_brand_price") or 0) * (it.get("ppl_count") or 1))

total_per_brand = sum(it.get("per_brand_price") or 0 for it in items)   # 이 거래처 청구액
total_calc = sum(calc_of(it) for it in items)                           # 전체 브랜드 청구 합
total_actual = sum(it.get("actual_cost") or 0 for it in items)
margin = total_calc - total_actual
mrate = round(margin / total_calc * 100, 1) if total_calc else 0

summary = pd.DataFrame([{
    "이름": it.get("name") or it.get("influencer_id"),
    "팔로워": f"{int(it.get('followers') or 0):,}",
    "평균조회수": f"{int(it.get('avg_views') or 0):,}",
    "업체당 단가": won(it.get("per_brand_price")),
    "PPL수": int(it.get("ppl_count") or 1),
    "청구(총)": won(calc_of(it)),
    "실제비용": won(it.get("actual_cost")),
    "마진%": (f"{round((calc_of(it)-(it.get('actual_cost') or 0))/calc_of(it)*100)}%" if calc_of(it) else "-"),
} for it in items])
st.dataframe(summary, use_container_width=True, hide_index=True)

mk = st.columns(4)
mk[0].metric("이 거래처 청구액 (Σ 업체당 단가)", won(total_per_brand))
mk[1].metric("전체 청구 합 (Σ 단가×PPL)", won(total_calc))
mk[2].metric("실제비용 합계", won(total_actual))
mk[3].metric("마진율", f"{mrate}%", delta=("좋음" if 13 <= mrate <= 32 else "확인"),
             delta_color=("normal" if mrate >= 13 else "inverse"))

# ── 마진율 선택 → 단가 자동 맞추기 ────────────────────────────
st.markdown("**마진율 맞추기** — 실제비용 기준으로 청구금액(업체당 단가)을 목표 마진율에 맞춰 채웁니다.")
mc = st.columns([1.2, 1.2, 3])
mopt = mc[0].selectbox("목표 마진율", ["15%", "20%", "25%", "30%", "직접입력"])
mval = mc[1].number_input("직접(%)", min_value=0, max_value=90, value=15, step=1) if mopt == "직접입력" else int(mopt[:-1])
if mc[2].button(f"🎯 마진 {mval}%로 업체당 단가 자동 맞추기"):
    m = mval / 100.0
    for it in items:
        act = it.get("actual_cost") or 0; ppl = it.get("ppl_count") or 1
        if act and m < 1:
            per = round_unit((act / (1 - m)) / ppl, unit)
            SUPA.table("inf_invoice_items").update(
                {"per_brand_price": per, "calc_price": per * ppl}).eq("id", it["id"]).execute()
    st.toast(f"마진 {mval}% 기준으로 단가를 맞췄습니다 ✓"); st.rerun()
if st.button("🔁 공식으로 업체당 단가 다시 제안"):
    for it in items:
        per = suggest_price(it.get("followers"), it.get("avg_views"), cpv, cpf, base, unit)
        SUPA.table("inf_invoice_items").update(
            {"per_brand_price": per, "calc_price": per * (it.get("ppl_count") or 1)}).eq("id", it["id"]).execute()
    st.toast("공식으로 단가를 다시 제안했습니다 ✓"); st.rerun()

# ── 행별 수정 · 첨부 ──────────────────────────────────────────
st.markdown("**행별 수정 · 첨부 (계좌·계약서·신분증)**")
for it in items:
    with st.expander(f"✎ {it.get('name') or it.get('influencer_id')} · 단가 {won(it.get('per_brand_price'))} × PPL {it.get('ppl_count') or 1} = {won(calc_of(it))} · 실제 {won(it.get('actual_cost'))}"):
        with st.form("edit" + it["id"]):
            e = st.columns(3)
            eper = e[0].number_input("업체당 단가", min_value=0, value=int(it.get("per_brand_price") or 0), step=100000)
            eppl = e[1].number_input("PPL 수", min_value=1, value=int(it.get("ppl_count") or 1), step=1)
            eact = e[2].number_input("실제비용(총)", min_value=0, value=int(it.get("actual_cost") or 0), step=100000)
            acc = st.text_input("계좌정보", value=it.get("account_info") or "")
            l = st.columns(3)
            curl = l[0].text_input("계약서 링크", value=it.get("contract_url") or "")
            iurl = l[1].text_input("신분증 링크", value=it.get("id_doc_url") or "")
            aurl = l[2].text_input("통장사본 링크", value=it.get("account_doc_url") or "")
            memo = st.text_input("메모", value=it.get("memo") or "")
            b = st.columns([1, 1, 4])
            if b[0].form_submit_button("저장", type="primary"):
                SUPA.table("inf_invoice_items").update({
                    "per_brand_price": int(eper), "ppl_count": int(eppl), "calc_price": int(eper) * int(eppl),
                    "actual_cost": int(eact), "account_info": acc, "contract_url": curl or None,
                    "id_doc_url": iurl or None, "account_doc_url": aurl or None, "memo": memo}).eq("id", it["id"]).execute()
                st.toast("저장됨 ✓"); st.rerun()
            if b[1].form_submit_button("삭제"):
                SUPA.table("inf_invoice_items").delete().eq("id", it["id"]).execute()
                st.toast("삭제됨 ✓"); st.rerun()
        miss = [n for n, u in [("계좌", it.get("account_info")), ("계약서", it.get("contract_url")),
                               ("신분증", it.get("id_doc_url"))] if not u]
        if miss:
            st.caption("⚠️ 세무 첨부 누락: " + ", ".join(miss))
        for nm, u in [("계약서", it.get("contract_url")), ("신분증", it.get("id_doc_url")), ("통장사본", it.get("account_doc_url"))]:
            if u:
                st.markdown(f"[{nm} 열기]({u})")

# ── 재무(지출) 연동 점검 (경고만) ─────────────────────────────
ce_out = SUPA.table("cash_events").select("amount").eq("project_id", pid).eq("direction", "out").execute().data
ce_out_sum = sum(c["amount"] or 0 for c in ce_out)
if ce_out_sum and ce_out_sum != total_actual:
    st.warning(f"⚠️ 재무 점검: 이 프로젝트 지출(나갈 돈) 합계 {won(ce_out_sum)} 와 인보이스 실제비용 합계 {won(total_actual)} 가 다릅니다. "
               "중복/누락일 수 있어요. (자동 연동은 하지 않습니다 — 추후 매칭 시 교정)")

# ── 거래처용 인보이스 ─────────────────────────────────────────
st.subheader("거래처용 인보이스")
st.caption("이 거래처에 청구하는 금액 = 인플루언서별 '업체당 단가' 합계입니다.")
supply = total_per_brand; vat = round(supply * 0.1); grand = supply + vat
rows_html = "".join(
    f"<tr><td>{(it.get('name') or it.get('influencer_id'))}</td><td>{it.get('platform') or '-'}</td>"
    f"<td style='text-align:right'>{int(it.get('followers') or 0):,}</td>"
    f"<td style='text-align:right'>{won(it.get('per_brand_price'))}</td></tr>" for it in items)
INV_CSS = ("body{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#e9edf3;margin:0}"
           ".page{width:210mm;min-height:297mm;padding:24mm 22mm;margin:12px auto;background:#fff;"
           "box-shadow:0 3px 18px rgba(20,30,50,.18);color:#1a1a1a;font-size:11pt;line-height:1.7}"
           "h1{text-align:center;letter-spacing:3px}table{width:100%;border-collapse:collapse;margin:14px 0}"
           "td,th{border:1px solid #bbb;padding:7px 9px}th{background:#f4f6fa}"
           "@media print{body{background:#fff}.page{box-shadow:none;margin:0}@page{size:A4;margin:0}}")
inv_html = (f"<html><head><meta charset='utf-8'><style>{INV_CSS}</style></head><body><div class='page'>"
            f"<h1>인 보 이 스 (INVOICE)</h1>"
            f"<p>거래처: <b>{brand_name}</b> · 프로젝트: {plabel}<br>발행일: {date.today().isoformat()}</p>"
            f"<table><tr><th>인플루언서</th><th>플랫폼</th><th>팔로워</th><th>청구 금액</th></tr>{rows_html}"
            f"<tr><th colspan='3' style='text-align:right'>공급가액</th><th style='text-align:right'>{won(supply)}</th></tr>"
            f"<tr><th colspan='3' style='text-align:right'>부가세(10%)</th><th style='text-align:right'>{won(vat)}</th></tr>"
            f"<tr><th colspan='3' style='text-align:right'>합계</th><th style='text-align:right'>{won(grand)}</th></tr></table>"
            f"<p style='margin-top:30px'>{BRANDSLAM['name']} · 사업자등록번호 {BRANDSLAM['biz']} · 대표 {BRANDSLAM['ceo']}<br>"
            f"{BRANDSLAM['addr']}</p></div></body></html>")

ic = st.columns(2)
ic[0].download_button("📄 인보이스 내려받기 (열어서 PDF로 인쇄)", inv_html,
                      file_name=f"인보이스_{brand_name}.html", mime="text/html", use_container_width=True)
if ic[1].button("💾 인보이스 저장 (보관함/콘솔에 표시)", use_container_width=True):
    SUPA.table("contracts").insert({
        "project_id": pid, "doc_type": "invoice", "counterparty": brand_name,
        "body": inv_html, "sign_status": "draft"}).execute()
    st.toast("인보이스를 Supabase에 저장했습니다 ✓ (계약서 보관함/콘솔에서 확인)")

# ── 세무용 분기별 내보내기 ────────────────────────────────────
st.subheader("세무용 내보내기 (분기별)")
allrows = SUPA.table("inf_invoice_items").select("*").order("created_at").execute().data
tdf = pd.DataFrame(allrows)
if not tdf.empty:
    tdf["created_at"] = pd.to_datetime(tdf["created_at"], errors="coerce")
    tdf["분기"] = tdf["created_at"].dt.to_period("Q").astype(str)
    quarters = sorted(tdf["분기"].dropna().unique(), reverse=True)
    qsel = st.selectbox("분기 선택", quarters) if quarters else None
    if qsel:
        sub = tdf[tdf["분기"] == qsel]
        export = pd.DataFrame({
            "등록일": sub["created_at"].dt.date.astype(str),
            "인플루언서ID": sub.get("influencer_id"),
            "이름": sub.get("name"),
            "실제비용": sub.get("actual_cost"),
            "계좌정보": sub.get("account_info"),
            "계약서링크": sub.get("contract_url"),
            "신분증링크": sub.get("id_doc_url"),
            "통장사본링크": sub.get("account_doc_url"),
        })
        st.caption(f"{qsel} · {len(export)}건 · 실제비용 합계 {won(sub['actual_cost'].sum())}")
        st.download_button("📥 분기 세무자료 CSV 내려받기",
                           export.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"세무_{qsel}.csv", mime="text/csv")
else:
    st.caption("내보낼 인보이스 항목이 없습니다.")
