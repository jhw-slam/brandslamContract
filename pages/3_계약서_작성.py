import os
import json

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="계약서 작성", layout="wide")

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

won = lambda n: "₩{:,}".format(int(n or 0))

def load_projects():
    return SUPA.table("projects").select("*").order("created_at").execute().data
def companies_map():
    return {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}

# ── A4 문서 스타일 (인쇄 시 실제 A4) ─────────────────────────
CSS = """
*{box-sizing:border-box}
body{background:#e9edf3;margin:0;font-family:'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif;}
.page{width:210mm;min-height:297mm;padding:24mm 22mm;margin:14px auto;background:#fff;
      box-shadow:0 3px 20px rgba(20,30,50,.18);color:#1a1a1a;line-height:1.85;font-size:11pt;}
.page h1{text-align:center;font-size:16pt;letter-spacing:3px;margin:0 0 26px;}
.page h2{font-size:12pt;margin:20px 0 8px;border-bottom:1px solid #ddd;padding-bottom:4px;}
.page p{margin:7px 0;}
.page .small{font-size:10pt;color:#333;}
.page .sig{margin-top:36px;line-height:2.3;border-top:1px solid #ccc;padding-top:18px;}
@media print{body{background:#fff}.page{box-shadow:none;margin:0}@page{size:A4;margin:0}}
"""

def wrap(inner):
    return "<html><head><meta charset='utf-8'><style>" + CSS + "</style></head><body>" + inner + "</body></html>"

# ── 브랜드사용 계약서 ─────────────────────────────────────────
def brand_html(v):
    supply = v["supply"]; vat = round(supply * 0.1); total = round(supply * 1.1)
    dep = round(total * v["dep"]); bal = round(total * v["bal"])
    inner = f"""<div class="page">
<h1>통합 홍보 대행 계약서</h1>
<p>본 계약은 <b>{v['a_name'] or '주식회사 ____'}</b>(이하 "A")와 <b>주식회사 브랜드슬램</b>(이하 "B") 간 포괄적 홍보 대행 서비스에 대한 '기본 계약'이며, 개별 서비스의 구체 내용은 '별첨: 서비스 내용'에 따른다.</p>
<p><b>제1조(목적)</b> A가 B에 의뢰하는 개별 서비스 수행에 관한 기본 권리·의무·책임을 정한다.</p>
<p><b>제2조(서비스 성격)</b> 무형의 콘텐츠 기획·운영 대행 용역이며, 계약 체결 및 선금 지급과 동시에 캠페인 구조 설계·콘텐츠 전략·브랜드 가이드 정리·인플루언서 풀 분석/매칭·운영 리소스 배정을 즉시 개시한다. (전자상거래법 제17조2항 청약철회 제한 대상일 수 있음)</p>
<p><b>제3조(개별 서비스 내용·기간)</b> 별첨에 따른다.</p>
<p><b>제4조(계약 금액·지급 조건)</b> 별첨에 따른다.</p>
<p><b>제5조(성과 비보장)</b> 업로드 수·조회수·매출·전환율 등은 보장되지 않으며, 최종 성과 책임은 A에 귀속된다.</p>
<p><b>제6조(환불·교체·조정)</b> 별첨에 따르며, A 동의 시 미집행분의 추가 섭외/차기 반영으로 조정할 수 있다.</p>
<p><b>제7조(직접 소통 제한)</b> 상대방 사전 서면 동의 없이 해당 인플루언서와 직접 계약·연락할 수 없다.</p>
<p><b>제8조(기밀 유지)</b> 계약 관련 정보를 기간 중·종료 후에도 외부에 공개할 수 없다.</p>
<p><b>제9조(분쟁·관할)</b> 대한민국 법을 준거법으로 하고 서울중앙지방법원을 전속 관할로 한다.</p>
<div class="sig">계약일: {v['sign_date'] or '____년 __월 __일'}<br><br>
A: {v['a_name'] or '____'} / 사업자등록번호 {v['a_biz'] or '__________'} / 대표이사 {v['a_ceo'] or '______'} (인)<br>
B: 주식회사 브랜드슬램 / 284-44-03016 / 대표이사 장현우 (인)</div>
<h2>별첨: 서비스 내용</h2>
<p class="small">· 서비스 명칭: {v['svc'] or '-'}<br>
· 대상 브랜드/제품: {v['target'] or '-'}<br>
· 플랫폼: {v['platform'] or '-'}  ·  운영 국가: {v['country'] or '-'}  ·  운영 규모: {v['scale'] or '-'}<br>
· 구성: {v['tier'] or '-'} / {v['qty'] or '-'} / {v['ctype'] or '-'}</p>
<p class="small">· 공급가액(VAT별도): <b>{won(supply)}</b>  ·  부가세(10%): {won(vat)}  ·  총 계약금액(VAT포함): <b>{won(total)}</b><br>
· 선금 {round(v['dep']*100)}%: <b>{won(dep)}</b> (계약 체결 후, 캠페인 시작 전)  ·  잔금 {round(v['bal']*100)}%: <b>{won(bal)}</b> ({v['bal_when'] or '리포트 제출 시'})<br>
· 결제 방식: {v['pay'] or '계좌이체'}  ·  계약 기간: 계약일로부터 {v['period'] or '3'}개월</p>
<p class="small">· 환불 가능: 리스트 전달 전, 계약일로부터 {v['refund'] or '3'}일 이내 전액 환불<br>
· 리스트 교체: 확정 전 전체의 {v['swap'] or '30'}%까지 교체 요청 가능<br>
· 미집행 교체: 리스트 전달일로부터 {v['noup'] or '90'}일 내 미업로드 인원은 추가 리스트로 교체 재진행(환불 불가)</p>
</div>"""
    return inner

# ── 인플루언서용 계약서 (영문) ────────────────────────────────
def creator_html(v):
    inner = f"""<div class="page">
<h1>Creator Campaign Agreement</h1>
<p>This Agreement defines the terms and conditions for the campaign between <b>BrandSlam Co., Ltd.</b> (the "Agency") and the Creator <b>{v['name'] or '____'}</b> (the "Creator").</p>
<h2>1. Campaign Overview</h2>
<p>· <b>Scope of Work:</b> Visit {v['store'] or 'the designated store'}, film, and upload content across the following platforms: Weibo, Douyin, Xiaohongshu, Bilibili, and WeChat Video (must remain public for at least six (6) months).</p>
<p>· <b>Compensation (Fee):</b> {v['payment'] or '____'} (All international transfer and PayPal fees shall be borne by the Creator).</p>
<h2>2. Core Terms</h2>
<p>· <b>Attendance & No-Show:</b> The Creator must arrive at the store on the scheduled date and time. Failure to attend without prior notice is deemed a "No-show," and payment will be fully forfeited. Schedule changes require the Agency's approval at least 48 hours in advance.</p>
<p>· <b>Ad & Content Rights:</b> The Agency may use the created content for marketing and promotional purposes, including paid advertisements (TikTok Spark Ads, Instagram Partnership Ads, etc.). The Creator must promptly provide the necessary ad codes upon request.</p>
<p>· <b>Exclusivity & Competitor Restriction:</b> For six (6) months from the visit date, the Creator shall not visit, feature, or create content for any competing pharmacy or pharmacy-affiliated wellness establishment. Violation may result in forfeiture of payment or a claim for damages.</p>
<p>· <b>Payment Criteria:</b> Payment will be processed on {v['pay_date'] or '____'}, provided the following are verified: content upload completion, content URL submission, signed agreement submission, and bank/PayPal information submission. Payment is refused if any term is breached or if content is deleted or set to private. If compensation in KRW is converted to a foreign currency, Woori Bank's standard exchange rate (기준환율) on the Agreement date applies.</p>
<div class="sig">Contract Date: {v['contract_date'] or '____ Year ____ Month ____ Day'}<br><br>
[ Signatures ]<br>
Agency — BrandSlam Co., Ltd. / CEO: Jang Hyunwoo / Signature: ______________________<br><br>
Creator — Full Name: {v['name'] or '____'} / Phone: {v['phone'] or '____'} / Visit Date: {v['visit_date'] or '____'} / Signature: ______________________</div>
</div>"""
    return inner

# ── 화면 ──────────────────────────────────────────────────────
st.title("계약서 작성")
projects = load_projects(); cmap = companies_map()
proj_opts = {"(연결 안 함)": None}
for p in projects:
    proj_opts[f"{cmap.get(p['company_id'],'')} · {p.get('product') or p.get('campaign') or p['brand']}"] = p

c1, c2 = st.columns(2)
kind = c1.radio("계약서 종류", ["브랜드사용", "인플루언서용"], horizontal=True)
proj_label = c2.selectbox("연결할 브랜드(계약현황에 저장됨)", list(proj_opts.keys()))
proj = proj_opts[proj_label]

left, right = st.columns([1, 1.3])

with left:
    st.subheader("머지값")
    if kind == "브랜드사용":
        a_name = st.text_input("브랜드사 상호", value=(cmap.get(proj["company_id"], "") if proj else ""))
        cc = st.columns(2)
        a_biz = cc[0].text_input("사업자등록번호"); a_ceo = cc[1].text_input("대표이사")
        sign_date = st.text_input("계약일", placeholder="2026-06-11")
        svc = st.text_input("서비스 명칭", value="인플루언서 시딩 홍보 대행 서비스")
        target = st.text_input("대상 브랜드/제품", value=(proj.get("product") if proj else ""))
        cc = st.columns(3)
        platform = cc[0].text_input("플랫폼", value="틱톡·인스타그램")
        country = cc[1].text_input("운영 국가", value="미국")
        scale = cc[2].text_input("운영 규모", value="")
        cc = st.columns(3)
        tier = cc[0].text_input("인플루언서 기준", value="나노·마이크로")
        qty = cc[1].text_input("수량", value="")
        ctype = cc[2].text_input("콘텐츠 유형", value="제품 리뷰")
        supply = st.number_input("공급가액(VAT별도)", min_value=0,
                                 value=int(proj["supply_amount"]) if proj else 0, step=100000)
        cc = st.columns(2)
        dep = cc[0].number_input("선금 비율", 0.0, 1.0, 0.5, 0.1)
        bal = cc[1].number_input("잔금 비율", 0.0, 1.0, 0.5, 0.1)
        bal_when = st.text_input("잔금 지급시점", value="리포트 제출 시")
        cc = st.columns(4)
        pay = cc[0].text_input("결제 방식", value="계좌이체")
        period = cc[1].text_input("기간(개월)", value="3")
        refund = cc[2].text_input("환불(일)", value="3")
        swap = cc[3].text_input("교체(%)", value="30")
        noup = st.text_input("미집행 교체(일)", value="90")
        v = dict(a_name=a_name, a_biz=a_biz, a_ceo=a_ceo, sign_date=sign_date, svc=svc,
                 target=target, platform=platform, country=country, scale=scale, tier=tier,
                 qty=qty, ctype=ctype, supply=supply, dep=dep, bal=bal, bal_when=bal_when,
                 pay=pay, period=period, refund=refund, swap=swap, noup=noup)
        inner = brand_html(v); counterparty = a_name or "브랜드사"
    else:
        name = st.text_input("Creator name")
        phone = st.text_input("Phone")
        payment = st.text_input("Compensation (Fee)", placeholder="예: USD 300 / ₩400,000")
        store = st.text_input("Store (매장)", value="the designated store")
        visit_date = st.text_input("Visit date", placeholder="2026-07-01")
        pay_date = st.text_input("Payment processing date", placeholder="2026-07-31")
        contract_date = st.text_input("Contract date", placeholder="2026-06-11")
        v = dict(name=name, phone=phone, payment=payment, store=store, visit_date=visit_date,
                 pay_date=pay_date, contract_date=contract_date)
        inner = creator_html(v); counterparty = name or "Creator"

with right:
    st.subheader("미리보기 (A4)")
    st.components.v1.html(wrap(inner), height=900, scrolling=True)

full_html = wrap(inner)
b1, b2 = st.columns(2)
b1.download_button("📄 계약서 내려받기 (열어서 PDF로 인쇄)", full_html,
                   file_name=f"계약서_{counterparty}.html", mime="text/html", use_container_width=True)
if b2.button("💾 이 브랜드 계약현황에 저장", type="primary", use_container_width=True):
    SUPA.table("contracts").insert({
        "project_id": proj["id"] if proj else None,
        "doc_type": "brand" if kind == "브랜드사용" else "creator",
        "counterparty": counterparty,
        "merge_values": json.loads(json.dumps(v, default=str)),
        "body": full_html, "sign_status": "draft",
    }).execute()
    if proj:
        st.success(f"저장 완료 — '{cmap.get(proj['company_id'],'')}' 계약현황(콘솔)에서 확인할 수 있어요.")
    else:
        st.success("저장 완료 (브랜드 미연결). 콘솔에 표시하려면 브랜드를 연결해 다시 저장하세요.")
