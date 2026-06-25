import os
import io

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

pid = st.session_state.get("pid")
p = next((x for x in projects if x["id"] == pid), None)
if not p:
    st.title("계약 콘솔")
    st.info("좌측에서 계약을 선택하거나, '➕ 계약 추가'로 새 계약을 등록하세요.")
    st.stop()

# ── 상세 ──────────────────────────────────────────────────────
st.title(f"{p['brand']} · {p.get('product') or ''}")
st.caption(f"{cmap.get(p['company_id'],'')} · 기간 {p.get('start_date') or '-'} ~ {p.get('end_date') or '-'}")

# 이 프로젝트의 송금 일정(여러 입금/출금)을 한 번에 합산 → 위 요약에 반영
ce = SUPA.table("cash_events").select("*").eq("project_id", p["id"]).order("due_date").execute().data
in_total = sum(e["amount"] or 0 for e in ce if e["direction"] == "in")
in_unpaid = sum(e["amount"] or 0 for e in ce if e["direction"] == "in" and not e["paid"])
out_total = sum(e["amount"] or 0 for e in ce if e["direction"] == "out")
out_unpaid = sum(e["amount"] or 0 for e in ce if e["direction"] == "out" and not e["paid"])

i = KEYS.index(p["stage"])
cols = st.columns(len(STAGES))
for n, (k, lab) in enumerate(STAGES):
    mark = "✅" if n < i else ("🟠" if n == i else "⚪")
    cols[n].markdown(f"<div style='text-align:center;font-size:12px'>{mark}<br>{lab}</div>", unsafe_allow_html=True)

# ── 매출 · 매입 통일 요약 (모두 cash_events 기준) ─────────────
# 매출 = 들어올/받은 돈(in) · 매입 = 나갈/나간 돈(out)
in_paid = sum(e["amount"] or 0 for e in ce if e["direction"] == "in" and e["paid"])
out_paid = sum(e["amount"] or 0 for e in ce if e["direction"] == "out" and e["paid"])
profit = in_total - out_total
margin = round(profit / in_total * 100) if in_total else 0
supply = p["supply_amount"] or in_total

st.markdown("**매출 (받을 돈)**")
r1 = st.columns(4)
r1[0].metric("매출 합계", won(in_total))
r1[1].metric("받은(입금완료)", won(in_paid))
r1[2].metric("미수금", won(in_unpaid))
r1[3].metric("단계", LABEL[p["stage"]])
st.markdown("**매입 (나갈 돈 · 실행사 지급 등)**")
r2 = st.columns(4)
r2[0].metric("매입 합계", won(out_total))
r2[1].metric("지급완료(나간)", won(out_paid))
r2[2].metric("미지급", won(out_unpaid))
r2[3].metric("이윤 · 이윤율", f"{won(profit)} · {margin}%")
st.caption(f"송금 {len(ce)}건 합산 · 매출 {won(in_total)}(미수 {won(in_unpaid)}) · 매입 {won(out_total)}(미지급 {won(out_unpaid)}) · 이윤 {won(profit)}"
           + (f" · 계약 공급가(VAT별도) {won(p['supply_amount'])}" if (p['supply_amount'] or 0) else ""))

# 선작업·미입금 경고 + 다음 할 일
_NEXT = {"LEAD": "계약서 발송", "SENT": "서명 받기", "SIGNED": "선금 청구·입금 확인",
         "DEPOSIT": "캠페인 진행", "PROGRESS": "성과보고서 업로드 → 잔금 청구",
         "BALANCE": "잔금 입금 확인", "SETTLED": "완료"}
if in_unpaid > 0 and p["stage"] in ("PROGRESS", "BALANCE", "SETTLED"):
    st.error(f"🔴 일은 진행 중인데 미입금 **{won(in_unpaid)}** 남아 있어요. 다음 할 일: **{_NEXT.get(p['stage'],'-')}**")
elif in_unpaid > 0:
    st.warning(f"🟠 미수 {won(in_unpaid)} · 다음 할 일: {_NEXT.get(p['stage'],'-')}")

# 성과보고서 링크
rc = st.columns([3, 1])
if p.get("report_url"):
    rc[0].markdown(f"📑 [성과보고서 열기]({p['report_url']})")
else:
    rc[0].caption("성과보고서 링크 없음 (아래 '금액·조건 수정'에서 등록)")

ca, cb, cc = st.columns(3)
if i < len(STAGES) - 1 and ca.button(f"▶ 「{STAGES[i+1][1]}」 진행", type="primary", use_container_width=True):
    nxt = KEYS[i + 1]; upd = {"stage": nxt}
    if nxt == "DEPOSIT": upd["deposit_paid"] = True
    if nxt == "SETTLED": upd["balance_paid"] = True
    SUPA.table("projects").update(upd).eq("id", p["id"]).execute()
    push_notif(p["id"], nxt, f"{STAGES[i+1][1]} 단계 알림이 발송되었습니다.")
    st.toast("Supabase 반영됨 ✓"); st.rerun()
if i > 0 and cb.button("◀ 되돌리기", use_container_width=True):
    prev = KEYS[i - 1]; upd = {"stage": prev}
    if p["stage"] == "DEPOSIT": upd["deposit_paid"] = False
    if p["stage"] == "SETTLED": upd["balance_paid"] = False
    SUPA.table("projects").update(upd).eq("id", p["id"]).execute()
    st.toast("Supabase 반영됨 ✓"); st.rerun()
if cc.button("📨 현재 단계 알림 보내기", use_container_width=True):
    push_notif(p["id"], p["stage"], f"[{LABEL[p['stage']]}] 단계를 완료해 주세요.")
    st.rerun()

# ── 금액·조건 수정 ────────────────────────────────────────────
with st.expander("금액 · 조건 수정"):
    with st.form("edit"):
        ns = st.number_input("공급가액(VAT별도)", value=int(supply), step=100000)
        dr = st.number_input("선금 비율", value=float(p["dep_ratio"] or 0.5), min_value=0.0, max_value=1.0, step=0.1)
        br = st.number_input("잔금 비율", value=float(p["bal_ratio"] or 0.5), min_value=0.0, max_value=1.0, step=0.1)
        terms = st.text_area("특이사항", value=p.get("terms") or "")
        report_url = st.text_input("성과보고서 링크 (외부 사이트 URL)", value=p.get("report_url") or "")
        if st.form_submit_button("저장"):
            SUPA.table("projects").update({"supply_amount": int(ns), "dep_ratio": dr,
                                           "bal_ratio": br, "terms": terms,
                                           "report_url": report_url or None}).eq("id", p["id"]).execute()
            st.toast("Supabase에 저장되었습니다 ✓"); st.rerun()

# ── 송금 일정 (이 프로젝트 · cash_events) ─────────────────────
st.subheader("송금 일정")
st.caption("여기서 입력하면 즉시 Supabase에 저장되고, '전체 송금 스케쥴' 페이지에도 합쳐져 보입니다. 위 요약 금액은 아래 항목들을 모두 합산한 값이에요.")
if ce:
    for e in ce:
        cols = st.columns([1.5, 1.1, 1.5, 1.5, 0.9, 1, 0.7])
        cols[0].write((e.get("due_date") or "-")[:10])
        cols[1].write("받을(매출)" if e["direction"] == "in" else "나갈(매입)")
        cols[2].write(e.get("category") or "")
        cols[3].write(won(e["amount"]))
        cols[4].write("✅ 완료" if e["paid"] else "⏳ 예정")
        if e["paid"]:
            if cols[5].button("되돌리기", key="undo" + e["id"]):
                SUPA.table("cash_events").update({"paid": False, "paid_date": None}).eq("id", e["id"]).execute()
                st.toast("예정으로 되돌림 ✓"); st.rerun()
        else:
            label = "입금완료" if e["direction"] == "in" else "지급완료"
            if cols[5].button(label, key="done" + e["id"], type="primary"):
                from datetime import date as _d
                SUPA.table("cash_events").update({"paid": True, "paid_date": _d.today().isoformat()}).eq("id", e["id"]).execute()
                st.toast("완료 처리됨 ✓ (전체 송금 스케쥴·종합상황판에 반영)"); st.rerun()
        if cols[6].button("삭제", key="ce" + e["id"]):
            SUPA.table("cash_events").delete().eq("id", e["id"]).execute()
            st.toast("삭제됨 ✓"); st.rerun()
else:
    st.caption("· 등록된 송금 일정이 없습니다. 아래에서 추가하세요.")

with st.form("ce_add", clear_on_submit=True):
    cf = st.columns([1.4, 1.4, 1.6, 1.3, 0.8])
    direction = cf[0].selectbox("구분", ["받을 돈(매출)", "나갈 돈(매입)"], label_visibility="collapsed")
    category = cf[1].selectbox("항목", ["선금", "잔금", "인보이스", "실행사 지급", "기타"], label_visibility="collapsed")
    amount = cf[2].number_input("금액", min_value=0, step=100000, label_visibility="collapsed")
    due = cf[3].date_input("예정일", label_visibility="collapsed")
    paid = cf[4].checkbox("완료")
    if st.form_submit_button("➕ 송금 일정 추가") and amount:
        SUPA.table("cash_events").insert({
            "project_id": p["id"], "direction": "in" if direction.startswith("받을") else "out",
            "category": category, "amount": int(amount), "due_date": due.isoformat(),
            "paid": paid, "paid_date": due.isoformat() if paid else None}).execute()
        st.toast("Supabase에 저장되었습니다 ✓"); st.rerun()

with st.expander("선금·잔금 예정 자동 만들기 (계약금액 기반)"):
    ac = st.columns(2)
    d1 = ac[0].date_input("선금 예정일", key="ad1")
    d2 = ac[1].date_input("잔금 예정일", key="ad2")
    if st.button("자동 생성"):
        rows = []
        if p["deposit_amount"]:
            rows.append({"project_id": p["id"], "direction": "in", "category": "선금",
                         "amount": int(p["deposit_amount"]), "due_date": d1.isoformat(), "paid": bool(p["deposit_paid"])})
        if p["balance_amount"]:
            rows.append({"project_id": p["id"], "direction": "in", "category": "잔금",
                         "amount": int(p["balance_amount"]), "due_date": d2.isoformat(), "paid": bool(p["balance_paid"])})
        if rows:
            SUPA.table("cash_events").insert(rows).execute()
            st.toast("선금·잔금 일정 생성됨 ✓"); st.rerun()

with st.expander("📥 지출 대량 등록 (엑셀/CSV 업로드)"):
    st.caption("아래 양식을 받아 채운 뒤 업로드하면, 이 프로젝트의 송금 일정으로 한 번에 등록됩니다. "
               "구분을 비우면 '나갈(매입)'로 들어갑니다.")
    TEMPLATE_COLS = ["구분", "항목", "제목", "금액", "예정일", "완료", "메모"]
    sample = pd.DataFrame([
        {"구분": "나갈", "항목": "실행사 지급", "제목": "강리즈 군단 1차", "금액": 3000000, "예정일": "2026-07-10", "완료": "N", "메모": ""},
        {"구분": "나갈", "항목": "기타", "제목": "배송비", "금액": 150000, "예정일": "2026-07-15", "완료": "Y", "메모": "택배"},
    ], columns=TEMPLATE_COLS)
    st.download_button("⬇️ 양식 내려받기 (CSV)", sample.to_csv(index=False).encode("utf-8-sig"),
                       file_name="지출_대량등록_양식.csv", mime="text/csv")

    up = st.file_uploader("채운 양식 업로드 (.csv / .xlsx)", type=["csv", "xlsx"])
    if up is not None:
        try:
            if up.name.lower().endswith(".xlsx"):
                df = pd.read_excel(up)  # openpyxl 필요
            else:
                df = pd.read_csv(io.BytesIO(up.getvalue()), encoding="utf-8-sig")
        except Exception as ex:
            st.error(f"파일을 읽지 못했습니다: {ex}"); df = None

        if df is not None:
            df.columns = [str(c).strip() for c in df.columns]
            miss = [c for c in ["항목", "금액"] if c not in df.columns]
            if miss:
                st.error(f"필수 열 누락: {', '.join(miss)} (양식의 헤더를 그대로 사용하세요)")
            else:
                rows, errs = [], []
                for idx, r in df.iterrows():
                    try:
                        amt = int(float(str(r.get("금액", 0)).replace(",", "").strip() or 0))
                        if amt <= 0:
                            continue
                        gubun = str(r.get("구분", "") or "").strip()
                        direction = "in" if gubun.startswith("받을") or gubun == "매출" else "out"
                        due_raw = str(r.get("예정일", "") or "").strip()[:10]
                        due = pd.to_datetime(due_raw).date().isoformat() if due_raw else None
                        paidv = str(r.get("완료", "") or "").strip().upper() in ("Y", "TRUE", "1", "O", "완료")
                        rows.append({
                            "project_id": p["id"], "direction": direction,
                            "category": str(r.get("항목", "") or "기타").strip(),
                            "title": str(r.get("제목", "") or "").strip() or None,
                            "amount": amt, "due_date": due, "paid": paidv,
                            "paid_date": due if paidv else None,
                            "memo": str(r.get("메모", "") or "").strip() or None})
                    except Exception as ex:
                        errs.append(f"{idx+2}행: {ex}")
                st.write(f"미리보기 — 등록 대상 {len(rows)}건" + (f" · 오류 {len(errs)}건" if errs else ""))
                if rows:
                    st.dataframe(pd.DataFrame([{
                        "구분": "받을(매출)" if x["direction"] == "in" else "나갈(매입)",
                        "항목": x["category"], "제목": x["title"], "금액": won(x["amount"]),
                        "예정일": x["due_date"], "완료": "Y" if x["paid"] else "N"} for x in rows]),
                        use_container_width=True, hide_index=True)
                for e in errs:
                    st.caption("⚠️ " + e)
                if rows and st.button(f"✅ {len(rows)}건 등록", type="primary"):
                    SUPA.table("cash_events").insert(rows).execute()
                    st.toast(f"{len(rows)}건 등록됨 ✓ (전체 송금 스케쥴·종합상황판에 반영)"); st.rerun()

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
