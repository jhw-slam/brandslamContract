import os

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="계약 콘솔", layout="wide")

# ── 비밀번호 게이트 (메인과 동일) ─────────────────────────────
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
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()

STAGES = [("LEAD", "제안·미팅"), ("SENT", "계약서 발송"), ("SIGNED", "서명 완료"),
          ("DEPOSIT", "선금 입금"), ("PROGRESS", "캠페인 진행"), ("BALANCE", "잔금 청구"),
          ("SETTLED", "정산 완료")]
KEYS = [k for k, _ in STAGES]; LABEL = dict(STAGES)
STATUS2STAGE = {"PAYMENT_PENDING": "SENT", "KICKOFF": "PROGRESS",
                "IN_PROGRESS": "PROGRESS", "COMPLETED": "SETTLED"}
won = lambda n: "₩{:,}".format(int(n or 0))

@st.cache_data(ttl=30)
def load_campaigns():
    return SUPA.table("campaigns").select(
        "order_number,brand_name,product_name,plan,status,plan_price,"
        "start_date,end_date,customer_name,created_at"
    ).order("created_at", desc=True).execute().data

def get_or_create_company(name):
    name = (name or "(미상)").strip()
    r = SUPA.table("companies").select("id").eq("name", name).limit(1).execute().data
    if r:
        return r[0]["id"]
    return SUPA.table("companies").insert({"name": name}).execute().data[0]["id"]

def get_project(order_number):
    r = SUPA.table("projects").select("*").eq("order_number", order_number).limit(1).execute().data
    return r[0] if r else None

def create_project(camp):
    cid = get_or_create_company(camp.get("brand_name"))
    payload = {
        "order_number": camp["order_number"], "company_id": cid,
        "brand": camp.get("brand_name") or "(미상)",
        "product": camp.get("product_name"), "region": "",
        "campaign": camp.get("product_name") or camp.get("plan"),
        "supply_amount": int(camp.get("plan_price") or 0),
        "stage": STATUS2STAGE.get(camp.get("status"), "LEAD"),
        "start_date": camp.get("start_date"), "end_date": camp.get("end_date"),
    }
    return SUPA.table("projects").insert(payload).execute().data[0]

def vendors_of(pid):
    return SUPA.table("project_vendors").select("*").eq("project_id", pid).order("created_at").execute().data

def notifs_of(pid):
    return SUPA.table("notifications").select("*").eq("project_id", pid).order("sent_at", desc=True).limit(15).execute().data

def outstanding(p):
    i = KEYS.index(p["stage"]); billed = paid = 0
    if i >= KEYS.index("SIGNED"):
        billed += p["deposit_amount"] or 0; paid += (p["deposit_amount"] or 0) if p["deposit_paid"] else 0
    if i >= KEYS.index("BALANCE"):
        billed += p["balance_amount"] or 0; paid += (p["balance_amount"] or 0) if p["balance_paid"] else 0
    return billed - paid

def push_notif(pid, stage, body, channel="email"):
    SUPA.table("notifications").insert({
        "project_id": pid, "stage": stage, "channel": channel,
        "template_code": "TPL_" + stage, "recipient": "브랜드 담당자",
        "body": body, "status": "sent"}).execute()

# ── 좌측: 회사(브랜드)별 캠페인 트리 ──────────────────────────
st.sidebar.title("회사 · 계약")
camps = load_campaigns()
groups = {}
for c in camps:
    groups.setdefault(c.get("brand_name") or "(미상)", []).append(c)
for brand, lst in groups.items():
    with st.sidebar.expander(f"{brand} · {len(lst)}건", expanded=True):
        for c in lst:
            lab = (c.get("product_name") or c.get("plan") or "계약")[:28]
            if st.button(lab, key="c" + c["order_number"], use_container_width=True):
                st.session_state.sel = c["order_number"]

sel = st.session_state.get("sel")
camp = next((c for c in camps if c["order_number"] == sel), None)
if not camp:
    st.title("계약 콘솔")
    st.info("좌측에서 계약을 선택하세요. (목록은 campaigns 실데이터)")
    st.stop()

# ── 아직 워크플로에 없는 계약 → 관리 시작 ─────────────────────
p = get_project(sel)
st.title(f"{camp.get('brand_name','')} · {camp.get('product_name','')}")
st.caption(f"주문번호 {sel} · 상태 {camp.get('status')} · 금액 {won(camp.get('plan_price'))} · 담당 {camp.get('customer_name') or '-'}")

if p is None:
    st.warning("이 계약은 아직 워크플로(projects)에 등록되지 않았습니다.")
    if st.button("▶ 이 계약 관리 시작", type="primary"):
        create_project(camp); st.rerun()
    st.stop()

# ── 단계 타임라인 ─────────────────────────────────────────────
i = KEYS.index(p["stage"])
cols = st.columns(len(STAGES))
for n, (k, lab) in enumerate(STAGES):
    mark = "✅" if n < i else ("🟠" if n == i else "⚪")
    cols[n].markdown(f"<div style='text-align:center;font-size:12px'>{mark}<br>{lab}</div>", unsafe_allow_html=True)

m = st.columns(4)
m[0].metric("총 계약금액(VAT포함)", won(p["total_amount"]))
m[1].metric("선금", won(p["deposit_amount"]) + (" ✓" if p["deposit_paid"] else " (대기)"))
m[2].metric("미수금", won(outstanding(p)))
m[3].metric("단계", LABEL[p["stage"]])

ca, cb, cc = st.columns(3)
if i < len(STAGES) - 1 and ca.button(f"▶ 「{STAGES[i+1][1]}」 진행", type="primary", use_container_width=True):
    nxt = KEYS[i + 1]; upd = {"stage": nxt}
    if nxt == "DEPOSIT": upd["deposit_paid"] = True
    if nxt == "SETTLED": upd["balance_paid"] = True
    SUPA.table("projects").update(upd).eq("id", p["id"]).execute()
    push_notif(p["id"], nxt, f"{STAGES[i+1][1]} 단계 알림이 발송되었습니다.")
    st.rerun()
if i > 0 and cb.button("◀ 되돌리기", use_container_width=True):
    prev = KEYS[i - 1]; upd = {"stage": prev}
    if p["stage"] == "DEPOSIT": upd["deposit_paid"] = False
    if p["stage"] == "SETTLED": upd["balance_paid"] = False
    SUPA.table("projects").update(upd).eq("id", p["id"]).execute()
    st.rerun()
if cc.button("📨 현재 단계 알림 보내기", use_container_width=True):
    push_notif(p["id"], p["stage"], f"[{LABEL[p['stage']]}] 단계를 완료해 주세요.")
    st.rerun()

# ── 수익 구조 (실행사 견적 → 이윤) ────────────────────────────
st.subheader("수익 구조")
vs = vendors_of(p["id"])
vtotal = sum(v["amount"] or 0 for v in vs)
supply = p["supply_amount"] or 0
mc = st.columns(3)
mc[0].metric("매출(공급가)", won(supply))
mc[1].metric("실행사 비용", won(vtotal))
rate = round((supply - vtotal) / supply * 100) if supply else 0
mc[2].metric("이윤 · 이윤율", f"{won(supply - vtotal)} · {rate}%")
for v in vs:
    vc = st.columns([5, 2, 1])
    vc[0].write(f"{v['vendor_name']}  ({v.get('file_url') or '파일 없음'})")
    vc[1].write(won(v["amount"]))
    if vc[2].button("삭제", key="d" + v["id"]):
        SUPA.table("project_vendors").delete().eq("id", v["id"]).execute(); st.rerun()
with st.form("vendor_add", clear_on_submit=True):
    f = st.columns([5, 2, 1])
    vn = f[0].text_input("실행사명", label_visibility="collapsed", placeholder="실행사명")
    va = f[1].number_input("금액", min_value=0, step=100000, label_visibility="collapsed")
    if f[2].form_submit_button("추가") and vn and va:
        SUPA.table("project_vendors").insert({"project_id": p["id"], "vendor_name": vn, "amount": int(va)}).execute()
        st.rerun()

# ── 금액·조건 수정 ────────────────────────────────────────────
with st.expander("금액 · 조건 수정"):
    with st.form("edit"):
        ns = st.number_input("공급가액(VAT별도)", value=int(supply), step=100000)
        dr = st.number_input("선금 비율", value=float(p["dep_ratio"] or 0.5), min_value=0.0, max_value=1.0, step=0.1)
        br = st.number_input("잔금 비율", value=float(p["bal_ratio"] or 0.5), min_value=0.0, max_value=1.0, step=0.1)
        terms = st.text_area("특이사항", value=p.get("terms") or "")
        if st.form_submit_button("저장"):
            SUPA.table("projects").update({"supply_amount": int(ns), "dep_ratio": dr,
                                           "bal_ratio": br, "terms": terms}).eq("id", p["id"]).execute()
            st.rerun()

# ── 계약서 미리보기 / 다운로드 ────────────────────────────────
st.subheader("계약서")
comp = SUPA.table("companies").select("*").eq("id", p["company_id"]).limit(1).execute().data
comp = comp[0] if comp else {}
total = p["total_amount"] or 0
body = f"""통합 홍보 대행 계약서

본 계약은 {comp.get('name','')}(이하 "A")와 주식회사 브랜드슬램(이하 "B") 간 홍보 대행 서비스에 대한 기본 계약이다.

[별첨: 서비스 내용]
· 브랜드/상품: {p['brand']} / {p.get('product','')}
· 계약 기간: {p.get('start_date') or '-'} ~ {p.get('end_date') or '-'}
· 공급가액(VAT별도): {won(supply)}
· 부가세(10%): {won(p['vat_amount'])}
· 총 계약금액(VAT포함): {won(total)}
· 선금: {won(p['deposit_amount'])}  /  잔금: {won(p['balance_amount'])}
· 결제 방식: {p.get('pay_method') or '계좌이체'}

[특이사항]
{p.get('terms') or '— 없음 —'}
"""
st.text(body)
doc_html = ("<html><head><meta charset='utf-8'><style>body{font-family:sans-serif;"
            "white-space:pre-wrap;padding:40px;line-height:1.8}</style></head><body>"
            + body.replace("<", "&lt;") + "</body></html>")
st.download_button("📄 계약서 HTML 내려받기 (열어서 PDF로 인쇄)", doc_html,
                   file_name=f"계약서_{p['brand']}.html", mime="text/html")

# ── 알림 발송 로그 ────────────────────────────────────────────
st.subheader("알림 발송 로그")
for nft in notifs_of(p["id"]):
    st.write(f"[{nft['channel']}] {nft['template_code']} → {nft['recipient']} · {nft['body']}")
