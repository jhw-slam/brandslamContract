import os
import datetime

import pandas as pd
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
won = lambda n: "₩{:,}".format(int(n or 0))

def load_projects():
    return SUPA.table("projects").select("*").order("created_at").execute().data

def companies_map():
    return {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}

def get_or_create_company(name):
    name = (name or "(미상)").strip()
    r = SUPA.table("companies").select("id").eq("name", name).limit(1).execute().data
    if r:
        return r[0]["id"]
    return SUPA.table("companies").insert({"name": name}).execute().data[0]["id"]

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


def parse_date(value):
    if not value:
        return None
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value))
    except Exception:
        try:
            return pd.to_datetime(value, errors="coerce").date()
        except Exception:
            return None


projects = load_projects()
cmap = companies_map()

# ── 좌측: 회사별 프로젝트 + 계약 추가 ─────────────────────────
st.sidebar.title("계약 (projects)")
with st.sidebar.expander("➕ 계약 추가", expanded=False):
    with st.form("add", clear_on_submit=True):
        cn = st.text_input("업체명")
        cm = st.text_input("캠페인/상품명")
        amt = st.number_input("공급가액(VAT별도)", min_value=0, step=100000)
        stg = st.selectbox("단계", KEYS, format_func=lambda k: LABEL[k])
        if st.form_submit_button("추가") and cn:
            cid = get_or_create_company(cn)
            SUPA.table("projects").insert({
                "company_id": cid, "brand": cn, "product": cm, "campaign": cm,
                "supply_amount": int(amt), "stage": stg}).execute()
            st.rerun()

groups = {}
for p in projects:
    groups.setdefault(cmap.get(p["company_id"], "(미상)"), []).append(p)
for comp, lst in groups.items():
    rev = sum(x["supply_amount"] or 0 for x in lst)
    with st.sidebar.expander(f"{comp} · {len(lst)}건", expanded=True):
        for p in lst:
            lab = (p.get("product") or p.get("campaign") or LABEL[p["stage"]])[:26]
            if st.button(f"{lab}", key="p" + p["id"], use_container_width=True):
                st.session_state.pid = p["id"]

if "sidebar_visible" not in st.session_state:
    st.session_state.sidebar_visible = True
btn_col, _ = st.columns([1, 19])
if btn_col.button("☰", help="사이드바 토글", key="sidebar_toggle"):
    st.session_state.sidebar_visible = not st.session_state.sidebar_visible
    st.rerun()
if not st.session_state.sidebar_visible:
    st.markdown(
        "<style>section[data-testid='stSidebar']{display:none !important;}"
        "div[data-testid='stAppViewContainer'] > div:first-child{margin-left:0 !important;}</style>",
        unsafe_allow_html=True,
    )

pid = st.session_state.get("pid")
p = next((x for x in projects if x["id"] == pid), None)
if not p:
    st.title("계약 콘솔")
    st.info("좌측에서 계약을 선택하거나, '➕ 계약 추가'로 새 계약을 등록하세요.")
    st.stop()

# ── 상세 ──────────────────────────────────────────────────────
st.title(f"{p['brand']} · {p.get('product') or ''}")
st.caption(f"{cmap.get(p['company_id'],'')} · 기간 {p.get('start_date') or '-'} ~ {p.get('end_date') or '-'}")
if p.get("deposit_date") or p.get("balance_date"):
    st.caption(
        f"선금 예정일 {p.get('deposit_date') or '-'} · 잔금 예정일 {p.get('balance_date') or '-'}"
    )

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

with st.expander("송금 캘린더", expanded=True):
    cal_items = []
    dp = parse_date(p.get("deposit_date"))
    bp = parse_date(p.get("balance_date"))
    if dp:
        cal_items.append({"구분": "선금 예정일", "일자": dp.isoformat(),
                          "금액": won(p["deposit_amount"]), "상태": "완료" if p["deposit_paid"] else "대기"})
    if bp:
        cal_items.append({"구분": "잔금 예정일", "일자": bp.isoformat(),
                          "금액": won(p["balance_amount"]), "상태": "완료" if p["balance_paid"] else "대기"})
    if cal_items:
        st.dataframe(pd.DataFrame(cal_items), use_container_width=True, hide_index=True)
    else:
        st.warning(
            "현재 이 계약에는 선금/잔금 예정일이 입력되어 있지 않아 송금 캘린더를 표시할 수 없습니다."
        )
        with st.expander("송금 일정 입력 안내"):
            st.write(
                "진행 중인 계약의 '금액 · 조건 수정'에서 선금 예정일과 잔금 예정일을 입력해주세요."
            )
            st.write(
                "입력한 날짜는 계약서 미리보기와 송금 캘린더에 반영됩니다."
            )
            st.write(
                "※ 데이터가 비어 있으면 먼저 해당 프로젝트의 금액·조건을 업데이트해 주세요."
            )

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

# ── 수익 구조 ─────────────────────────────────────────────────
st.subheader("수익 구조")
vs = vendors_of(p["id"]); vtotal = sum(v["amount"] or 0 for v in vs); supply = p["supply_amount"] or 0
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
        dd_default = parse_date(p.get("deposit_date")) or datetime.date.today()
        bd_default = parse_date(p.get("balance_date")) or datetime.date.today()
        dd = st.date_input("선금 예정일", value=dd_default)
        bd = st.date_input("잔금 예정일", value=bd_default)
        terms = st.text_area("특이사항", value=p.get("terms") or "")
        if st.form_submit_button("저장"):
            upd = {"supply_amount": int(ns), "dep_ratio": dr,
                   "bal_ratio": br, "terms": terms,
                   "deposit_date": dd.isoformat(), "balance_date": bd.isoformat()}
            SUPA.table("projects").update(upd).eq("id", p["id"]).execute()
            st.rerun()

# ── 계약서 미리보기 / 다운로드 ────────────────────────────────
st.subheader("계약서")
total = p["total_amount"] or 0
body = f"""통합 홍보 대행 계약서

본 계약은 {cmap.get(p['company_id'],'')}(이하 "A")와 주식회사 브랜드슬램(이하 "B") 간 홍보 대행 서비스에 대한 기본 계약이다.

[별첨: 서비스 내용]
· 브랜드/상품: {p['brand']} / {p.get('product') or ''}
· 계약 기간: {p.get('start_date') or '-'} ~ {p.get('end_date') or '-'}
· 공급가액(VAT별도): {won(supply)}
· 부가세(10%): {won(p['vat_amount'])}
· 총 계약금액(VAT포함): {won(total)}
· 선금: {won(p['deposit_amount'])}  /  선금 지급예정일: {p.get('deposit_date') or '-'}
· 잔금: {won(p['balance_amount'])}  /  잔금 지급예정일: {p.get('balance_date') or '-'}
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

# 「계약서 작성」 탭에서 이 브랜드로 저장한 최종 계약서들
saved = SUPA.table("contracts").select("*").eq("project_id", p["id"]).order("created_at", desc=True).execute().data
if saved:
    st.markdown("**저장된 계약서** (계약서 작성 탭에서 저장됨)")
    for s in saved:
        cols = st.columns([6, 2])
        cols[0].write(f"· [{s.get('doc_type') or '-'}] {s.get('counterparty') or ''} · {(s.get('created_at') or '')[:10]} · {s.get('sign_status') or 'draft'}")
        if s.get("body"):
            cols[1].download_button("내려받기", s["body"], file_name=f"계약서_{s.get('counterparty') or p['brand']}.html",
                                    mime="text/html", key="sv" + s["id"])

# ── 알림 발송 로그 ────────────────────────────────────────────
st.subheader("알림 발송 로그")
for nft in notifs_of(p["id"]):
    st.write(f"[{nft['channel']}] {nft['template_code']} → {nft['recipient']} · {nft['body']}")
