import os
import io
from datetime import date

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="인플루언서 인보이스", layout="wide")

BRANDSLAM = {"name": "주식회사 브랜드슬램", "biz": "284-88-03016", "ceo": "장현우",
             "addr": "서울시 강남구 테헤란로 7길 11, 한덕빌딩 9층 902호"}

# ── 비밀번호 게이트 ───────────────────────────────────────────
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
    # 인플루언서 데이터(다른 Supabase 프로젝트)용 2번째 클라이언트
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

def calc_price(followers, views, cpv, cpf, base, unit):
    return round_unit(base + (views or 0) * cpv + (followers or 0) * cpf, unit)

def items_of(pid):
    return SUPA.table("inf_invoice_items").select("*").eq("project_id", pid).order("created_at").execute().data

# ── 화면 ──────────────────────────────────────────────────────
st.title("인플루언서 인보이스")
st.caption("인플루언서 영향력(팔로워·평균조회수)으로 적정 제안가(산출가)를 계산해 거래처용 인보이스를 만듭니다. "
           "실제비용은 따로 입력해 재무·세무 자료로 모읍니다.")

opts = projects_map()
if not opts:
    st.warning("프로젝트가 없습니다. '계약 콘솔'에서 먼저 계약(프로젝트)을 추가하세요."); st.stop()
plabel = st.selectbox("프로젝트 선택", list(opts.keys()))
pid, brand_name = opts[plabel]

if infdb() is None:
    st.info("ℹ️ 인플루언서 자동검색을 쓰려면 Railway에 INFLU_SUPABASE_URL / INFLU_SUPABASE_KEY (DB_confidential 프로젝트) 를 추가하세요. "
            "지금은 수동 입력으로도 등록할 수 있어요.")

# ── 산출 공식 설정 ────────────────────────────────────────────
with st.expander("⚙️ 산출가 공식 설정"):
    f = st.columns(4)
    cpv = f[0].number_input("조회수 단가(원/view)", min_value=0, value=30, step=5)
    cpf = f[1].number_input("팔로워 단가(원/follower)", min_value=0, value=5, step=1)
    base = f[2].number_input("기본가(base)", min_value=0, value=0, step=100000)
    unit = f[3].number_input("반올림 단위(원)", min_value=1, value=10000, step=1000)
    st.caption("산출가 = (base + 평균조회수×조회수단가 + 팔로워×팔로워단가) 를 반올림 단위로 정리")

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
            cp = calc_price(fol, av, cpv, cpf, base, unit)
            SUPA.table("inf_invoice_items").insert({
                "project_id": pid, "influencer_id": r["influencer_id"], "name": r.get("name"),
                "platform": r.get("platform"), "followers": fol, "avg_views": av,
                "calc_price": cp, "actual_cost": 0,
                "account_doc_url": r.get("account_url")}).execute()
            st.toast(f"{r.get('name')} 등록 · 산출가 {won(cp)} (평균조회수 {av:,})"); st.rerun()

with tab_manual:
    with st.form("manual_add", clear_on_submit=True):
        mc = st.columns(4)
        mname = mc[0].text_input("이름/핸들")
        mfol = mc[1].number_input("팔로워", min_value=0, step=1000)
        mviews = mc[2].number_input("평균조회수", min_value=0, step=1000)
        mactual = mc[3].number_input("실제비용", min_value=0, step=100000)
        if st.form_submit_button("등록") and mname:
            cp = calc_price(mfol, mviews, cpv, cpf, base, unit)
            SUPA.table("inf_invoice_items").insert({
                "project_id": pid, "influencer_id": None, "name": mname, "platform": "manual",
                "followers": int(mfol), "avg_views": int(mviews), "calc_price": cp,
                "actual_cost": int(mactual)}).execute()
            st.toast(f"{mname} 등록 · 산출가 {won(cp)}"); st.rerun()

# ── 등록된 인플루언서 / 비용표 ────────────────────────────────
items = items_of(pid)
st.subheader("비용 산출표")
if not items:
    st.info("아직 등록된 인플루언서가 없습니다. 위에서 등록하세요."); st.stop()

total_calc = sum(it["calc_price"] or 0 for it in items)
total_actual = sum(it["actual_cost"] or 0 for it in items)
margin = total_calc - total_actual
mrate = round(margin / total_calc * 100, 1) if total_calc else 0

summary = pd.DataFrame([{
    "이름": it.get("name") or it.get("influencer_id"),
    "팔로워": f"{int(it.get('followers') or 0):,}",
    "평균조회수": f"{int(it.get('avg_views') or 0):,}",
    "산출가(제안)": won(it["calc_price"]),
    "실제비용": won(it["actual_cost"]),
} for it in items])
st.dataframe(summary, use_container_width=True, hide_index=True)

mk = st.columns(4)
mk[0].metric("산출가 합계 (거래처 표시가)", won(total_calc))
mk[1].metric("실제비용 합계", won(total_actual))
mk[2].metric("마진", won(margin))
mk[3].metric("마진율", f"{mrate}%", delta=("목표 근접" if 13 <= mrate <= 17 else "목표(15%)와 차이"),
             delta_color=("normal" if 13 <= mrate <= 17 else "inverse"))

hc = st.columns(2)
if hc[0].button("🎯 마진 15%로 자동 맞추기 (산출가 = 실제 ÷ 0.85)"):
    for it in items:
        newcalc = round_unit((it["actual_cost"] or 0) / 0.85, unit)
        SUPA.table("inf_invoice_items").update({"calc_price": newcalc}).eq("id", it["id"]).execute()
    st.toast("산출가를 마진 15% 기준으로 재설정했습니다 ✓"); st.rerun()
if hc[1].button("🔁 현재 공식으로 산출가 전체 재계산"):
    for it in items:
        SUPA.table("inf_invoice_items").update({
            "calc_price": calc_price(it.get("followers"), it.get("avg_views"), cpv, cpf, base, unit)}).eq("id", it["id"]).execute()
    st.toast("공식으로 산출가를 재계산했습니다 ✓"); st.rerun()

# 행별 편집(산출가/실제/첨부)
st.markdown("**행별 수정 · 첨부 (계좌·계약서·신분증)**")
for it in items:
    with st.expander(f"✎ {it.get('name') or it.get('influencer_id')} · 산출 {won(it['calc_price'])} · 실제 {won(it['actual_cost'])}"):
        with st.form("edit" + it["id"]):
            e = st.columns(2)
            ecalc = e[0].number_input("산출가(제안)", min_value=0, value=int(it["calc_price"] or 0), step=10000)
            eact = e[1].number_input("실제비용", min_value=0, value=int(it["actual_cost"] or 0), step=100000)
            acc = st.text_input("계좌정보", value=it.get("account_info") or "")
            l = st.columns(3)
            curl = l[0].text_input("계약서 링크", value=it.get("contract_url") or "")
            iurl = l[1].text_input("신분증 링크", value=it.get("id_doc_url") or "")
            aurl = l[2].text_input("통장사본 링크", value=it.get("account_doc_url") or "")
            memo = st.text_input("메모", value=it.get("memo") or "")
            bcol = st.columns([1, 1, 4])
            if bcol[0].form_submit_button("저장", type="primary"):
                SUPA.table("inf_invoice_items").update({
                    "calc_price": int(ecalc), "actual_cost": int(eact), "account_info": acc,
                    "contract_url": curl or None, "id_doc_url": iurl or None,
                    "account_doc_url": aurl or None, "memo": memo}).eq("id", it["id"]).execute()
                st.toast("저장됨 ✓"); st.rerun()
            if bcol[1].form_submit_button("삭제"):
                SUPA.table("inf_invoice_items").delete().eq("id", it["id"]).execute()
                st.toast("삭제됨 ✓"); st.rerun()
        # 첨부 누락 경고 / 링크 열기
        miss = [n for n, u in [("계좌", it.get("account_info")), ("계약서", it.get("contract_url")),
                               ("신분증", it.get("id_doc_url"))] if not u]
        if miss:
            st.caption("⚠️ 세무 첨부 누락: " + ", ".join(miss))
        for nm, u in [("계약서", it.get("contract_url")), ("신분증", it.get("id_doc_url")), ("통장사본", it.get("account_doc_url"))]:
            if u:
                st.markdown(f"[{nm} 열기]({u})")

# ── 재무(지출) 연동 점검 (경고만, 자동연동 X) ─────────────────
ce_out = SUPA.table("cash_events").select("amount").eq("project_id", pid).eq("direction", "out").execute().data
ce_out_sum = sum(c["amount"] or 0 for c in ce_out)
if ce_out_sum and ce_out_sum != total_actual:
    st.warning(f"⚠️ 재무 점검: 이 프로젝트의 지출(나갈 돈) 합계 {won(ce_out_sum)} 와 인보이스 실제비용 합계 {won(total_actual)} 가 다릅니다. "
               "중복 입력이거나 누락일 수 있어요. (지금은 자동 연동하지 않습니다 — 추후 매칭 시 교정)")

# ── 인보이스 생성 / 저장 ──────────────────────────────────────
st.subheader("거래처용 인보이스")
supply = total_calc; vat = round(supply * 0.1); grand = supply + vat
rows_html = "".join(
    f"<tr><td>{(it.get('name') or it.get('influencer_id'))}</td><td>{it.get('platform') or '-'}</td>"
    f"<td style='text-align:right'>{int(it.get('followers') or 0):,}</td>"
    f"<td style='text-align:right'>{won(it['calc_price'])}</td></tr>" for it in items)
INV_CSS = ("body{font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;background:#e9edf3;margin:0}"
           ".page{width:210mm;min-height:297mm;padding:24mm 22mm;margin:12px auto;background:#fff;"
           "box-shadow:0 3px 18px rgba(20,30,50,.18);color:#1a1a1a;font-size:11pt;line-height:1.7}"
           "h1{text-align:center;letter-spacing:3px}table{width:100%;border-collapse:collapse;margin:14px 0}"
           "td,th{border:1px solid #bbb;padding:7px 9px}th{background:#f4f6fa}"
           "@media print{body{background:#fff}.page{box-shadow:none;margin:0}@page{size:A4;margin:0}}")
inv_html = (f"<html><head><meta charset='utf-8'><style>{INV_CSS}</style></head><body><div class='page'>"
            f"<h1>인 보 이 스 (INVOICE)</h1>"
            f"<p>거래처: <b>{brand_name}</b> · 프로젝트: {plabel}<br>발행일: {date.today().isoformat()}</p>"
            f"<table><tr><th>인플루언서</th><th>플랫폼</th><th>팔로워</th><th>제안 금액</th></tr>{rows_html}"
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
