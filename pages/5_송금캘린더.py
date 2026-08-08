import os
import io
import re
import calendar
from datetime import date, timedelta

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="송금 캘린더", layout="wide")

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
def won_short(n):
    n = int(n or 0)
    if n >= 100000000:
        return f"{n/100000000:.1f}억"
    if n >= 10000:
        return f"{n//10000:,}만"
    return f"{n:,}"

SLAB = {"LEAD": "제안·미팅", "SENT": "계약서 발송", "SIGNED": "서명 완료", "DEPOSIT": "선금 입금",
        "PROGRESS": "캠페인 진행", "BALANCE": "잔금 청구", "SETTLED": "정산 완료"}
CATS = ["선금", "잔금", "인보이스", "실행사 지급", "은행거래", "기타"]

def projects_map():
    projs = SUPA.table("projects").select("id,brand,product,company_id,stage").execute().data
    comp = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    label = {p["id"]: f"{comp.get(p['company_id'],'')} · {p.get('product') or p['brand']}" for p in projs}
    return projs, label

def load_events():
    return SUPA.table("cash_events").select("*").order("due_date").execute().data

# ── 은행 거래내역 파싱 (은행마다 헤더가 달라서 유연하게 컬럼을 찾음) ──
_COLKEY = {
    "date": ["거래일시", "거래일자", "거래일", "일자", "날짜", "이용일자", "승인일자"],
    "deposit": ["입금액", "입금", "맡기신금액", "들어온금액"],
    "withdraw": ["출금액", "출금", "찾으신금액", "나간금액", "이용금액"],
    "desc": ["거래내용", "내용", "적요", "거래구분", "받으신분", "보내신분", "가맹점명", "메모", "비고"],
    "balance": ["거래후잔액", "잔액"],
}

def _find_col(columns, keys):
    for col in columns:
        c = str(col).strip()
        for k in keys:
            if k in c:
                return col
    return None

def _to_amount(v):
    if v is None:
        return 0
    s = str(v).strip().replace(",", "").replace("₩", "")
    if s in ("", "-", "nan", "None"):
        return 0
    try:
        return abs(int(float(s)))
    except Exception:
        return 0

# 급여 지급 대상 — 이 6명 이름이 메모에 포함된 거래는 절대 가져오지 않는다 (인원 변경 시 이 목록만 수정).
STAFF_PAYROLL_NAMES = ["김선재", "정다영", "양혜준", "구정회", "박솔", "장현우"]

def looks_like_payroll(desc):
    """메모에 사내 직원 이름이 포함되면 급여로 간주해 무조건 제외 (금액 크기와 무관 — 소액 인플루언서 해외송금은 별개)."""
    s = re.sub(r"\s+", "", (desc or ""))
    return any(name in s for name in STAFF_PAYROLL_NAMES)

def parse_bank_rows(df):
    """df: 은행에서 그대로 다운받은 표. 컬럼명이 은행마다 달라서 키워드로 유연하게 찾는다."""
    cols = list(df.columns)
    c_date = _find_col(cols, _COLKEY["date"])
    c_dep = _find_col(cols, _COLKEY["deposit"])
    c_wd = _find_col(cols, _COLKEY["withdraw"])
    c_desc = _find_col(cols, _COLKEY["desc"])
    if not c_date or not (c_dep or c_wd):
        return None, f"필수 컬럼을 못 찾았습니다 (날짜: {c_date}, 입금: {c_dep}, 출금: {c_wd}). 헤더를 확인해주세요."
    out = []
    for _, r in df.iterrows():
        dep = _to_amount(r.get(c_dep)) if c_dep else 0
        wd = _to_amount(r.get(c_wd)) if c_wd else 0
        if dep == 0 and wd == 0:
            continue
        direction, amount = ("in", dep) if dep > wd else ("out", wd)
        try:
            d = pd.to_datetime(str(r.get(c_date))[:10], errors="coerce")
            due = d.date().isoformat() if pd.notna(d) else None
        except Exception:
            due = None
        desc = str(r.get(c_desc)).strip() if c_desc else ""
        out.append({"direction": direction, "amount": amount, "due_date": due, "desc": desc})
    return out, None

def dedup_against_existing(rows, existing):
    seen_existing = {(e["direction"], e.get("amount") or 0, (e.get("due_date") or "")[:10]) for e in existing}
    seen_batch = set()
    for r in rows:
        key = (r["direction"], r["amount"], r["due_date"] or "")
        r["_dup_existing"] = key in seen_existing
        r["_dup_batch"] = key in seen_batch
        seen_batch.add(key)
    return rows

# ── 홈택스 세금계산서 파싱 (매출 목록 .xls/.csv) ──────────────
_APPROVAL = re.compile(r"\d{8}-\d{8}-\w+")
_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_BIZ = re.compile(r"\d{3}-\d{2}-\d{5}")
_MONEY = re.compile(r"-?\d{1,3}(?:,\d{3})+")

def _guess_name(cells):
    for c in cells:
        if any(t in c for t in ["(주)", "주식회사", "㈜"]):
            return c
    for c in cells:
        if re.search(r"[가-힣]", c) and len(c) >= 2 and not re.search(
                r"(작성|발급|전송|일자|공급|등록|대표|번호|종류|유형|인터넷|일반|수정|계산서|명세|합계|세액)", c):
            return c
    return None

def parse_hometax(rows):
    """rows: list[list[cell]] → list of tax-invoice dicts (position-independent)."""
    out, cur = [], None
    for row in rows:
        cells = [str(c).strip() for c in row if str(c).strip() and str(c).strip().lower() != "nan"]
        if not cells:
            continue
        joined = " ".join(cells)
        appr = _APPROVAL.search(joined)
        if appr:
            if cur:
                out.append(cur)
            cur = {"approval_no": appr.group(), "raw": joined[:500]}
            d = _DATE.search(joined); cur["write_date"] = d.group() if d else None
            b = _BIZ.search(joined); cur["buyer_biz_no"] = b.group() if b else None
            cur["buyer_name"] = _guess_name(
                [c for c in cells if not _DATE.search(c) and not _BIZ.search(c) and not _APPROVAL.search(c)])
            cur["kind"] = "수정" if "수정" in joined else "일반"
        elif cur is not None and _MONEY.search(joined):
            nums = [int(x.replace(",", "")) for x in _MONEY.findall(joined)]
            d2 = _DATE.findall(joined)
            if d2:
                cur["issue_date"] = d2[0]
            if len(nums) >= 1: cur["total_amount"] = nums[0]
            if len(nums) >= 2: cur["supply_amount"] = nums[1]
            if len(nums) >= 3: cur["vat"] = nums[2]
            out.append(cur); cur = None
    if cur:
        out.append(cur)
    return out

def reconcile_cancellations():
    """음수(수정/취소) 계산서를 같은 거래처·같은 절대금액·날짜 근접인 양수(원본)와 짝지어
    둘 다 canceled=True 로 표시. 원본이 cash_event에 연결돼 있었으면 연결도 해제."""
    inv = SUPA.table("tax_invoices").select("*").execute().data
    negs = [v for v in inv if (v.get("total_amount") or 0) < 0 and not v.get("canceled")]
    pos = [v for v in inv if (v.get("total_amount") or 0) > 0 and not v.get("canceled")]
    used, pairs = set(), 0

    def _gap(a, b):
        try:
            return abs((date.fromisoformat(a["write_date"]) - date.fromisoformat(b["write_date"])).days)
        except Exception:
            return 999

    for n in negs:
        target = abs(n["total_amount"] or 0)
        cands = [p for p in pos if p["approval_no"] not in used
                 and p.get("buyer_biz_no") == n.get("buyer_biz_no")
                 and (p.get("total_amount") or 0) == target]
        cands.sort(key=lambda p: _gap(p, n))
        if cands:
            p = cands[0]
            used.add(p["approval_no"]); used.add(n["approval_no"])
            for ap in (p["approval_no"], n["approval_no"]):
                SUPA.table("tax_invoices").update({"canceled": True}).eq("approval_no", ap).execute()
            if p.get("matched_cash_event_id"):
                SUPA.table("cash_events").update(
                    {"tax_issued": False, "tax_invoice_no": None}).eq("id", p["matched_cash_event_id"]).execute()
                SUPA.table("tax_invoices").update(
                    {"matched_cash_event_id": None}).eq("approval_no", p["approval_no"]).execute()
            pairs += 1
    return pairs

def auto_match_tax():
    """살아있는(취소 안 됨·미연결) 발행분을 '받을' 일정과 금액·날짜로 1:1 자동 매칭."""
    invs = [v for v in SUPA.table("tax_invoices").select("*").execute().data
            if (v.get("total_amount") or 0) > 0 and not v.get("canceled") and not v.get("matched_cash_event_id")]
    rcv = [e for e in SUPA.table("cash_events").select("*").eq("direction", "in").execute().data
           if not e.get("tax_issued")]
    matched = 0
    for v in invs:
        tot = v.get("total_amount") or 0; sup = v.get("supply_amount") or 0
        try:
            wdate = date.fromisoformat(v["write_date"]) if v.get("write_date") else None
        except Exception:
            wdate = None
        cand = []
        for e in rcv:
            if (e["amount"] or 0) not in (tot, sup):
                continue
            ok = True
            if wdate and e.get("due_date"):
                try:
                    ok = abs((date.fromisoformat(e["due_date"][:10]) - wdate).days) <= 45
                except Exception:
                    ok = True
            if ok:
                cand.append(e)
        if len(cand) == 1:
            e = cand[0]
            SUPA.table("cash_events").update(
                {"tax_issued": True, "tax_invoice_no": v["approval_no"]}).eq("id", e["id"]).execute()
            SUPA.table("tax_invoices").update(
                {"matched_cash_event_id": e["id"]}).eq("approval_no", v["approval_no"]).execute()
            rcv = [x for x in rcv if x["id"] != e["id"]]
            matched += 1
    return matched

projs, plabel = projects_map()
pstage = {p["id"]: SLAB.get(p.get("stage"), p.get("stage") or "-") for p in projs}
label2id = {plabel[p["id"]]: p["id"] for p in projs}
events_all = load_events()

st.title("송금 캘린더")

# ── 상단 컨트롤 바 (한 줄) ────────────────────────────────────
top = st.columns([1.2, 1.9, 1.2, 1.1])
period = top[0].selectbox("기간", ["최근 3개월", "최근 6개월", "올해", "전체", "직접 선택"], index=0)
proj_opts = ["전체 프로젝트"] + [plabel[p["id"]] for p in projs] + ["(프로젝트 없음)"]
projsel = top[1].selectbox("프로젝트", proj_opts)
flt = top[2].selectbox("상태", ["전체", "미입금", "입금완료", "미지급"])
view = top[3].radio("보기", ["달력", "리스트", "세금계산서"], horizontal=True)

today = date.today()
start, end = today - timedelta(days=90), None
if period == "최근 6개월":
    start = today - timedelta(days=180)
elif period == "올해":
    start = date(today.year, 1, 1)
elif period == "전체":
    start = None
elif period == "직접 선택":
    dr = st.date_input("직접 기간", value=(today - timedelta(days=90), today))
    if isinstance(dr, (list, tuple)) and len(dr) == 2:
        start, end = dr[0], dr[1]

# ── 동작 버튼(팝오버): 일정 추가 · 지출 대량 업로드 · 세금계산서 ─
act = st.columns([1.1, 1.5, 1.8, 1.8, 2.2])
with act[0].popover("➕ 일정 추가", use_container_width=True):
    with st.form("add_ev", clear_on_submit=True):
        popts = {"(프로젝트 없음)": None}
        for p in projs:
            popts[plabel[p["id"]]] = p["id"]
        pj = st.selectbox("프로젝트", list(popts.keys()))
        cc = st.columns(2)
        direction = cc[0].selectbox("구분", ["받을 돈(매출)", "나갈 돈(매입)"])
        category = cc[1].selectbox("항목", CATS)
        cc2 = st.columns(2)
        amount = cc2[0].number_input("금액", min_value=0, step=100000)
        due = cc2[1].date_input("예정일", value=today)
        title = st.text_input("메모/제목", placeholder="예: OWM 선금 청구")
        paid = st.checkbox("완료(입금/지급)")
        if st.form_submit_button("추가", type="primary") and amount:
            SUPA.table("cash_events").insert({
                "project_id": popts[pj], "direction": "in" if direction.startswith("받을") else "out",
                "category": category, "title": title, "amount": int(amount),
                "due_date": due.isoformat(), "paid": paid,
                "paid_date": due.isoformat() if paid else None}).execute()
            st.rerun()

with act[1].popover("📥 지출 대량 업로드", use_container_width=True):
    st.caption("양식을 받아 채운 뒤 업로드하면 한 번에 등록됩니다. 구분을 비우면 '나갈(매입)'.")
    bpopts = {"(프로젝트 없음)": None}
    for p in projs:
        bpopts[plabel[p["id"]]] = p["id"]
    bpj = st.selectbox("등록할 프로젝트", list(bpopts.keys()), key="bulk_pj")
    cols_t = ["구분", "항목", "제목", "금액", "예정일", "완료", "메모"]
    sample = pd.DataFrame([
        {"구분": "나갈", "항목": "실행사 지급", "제목": "강리즈 군단 1차", "금액": 3000000, "예정일": "2026-07-10", "완료": "N", "메모": ""},
        {"구분": "나갈", "항목": "기타", "제목": "배송비", "금액": 150000, "예정일": "2026-07-15", "완료": "Y", "메모": "택배"},
    ], columns=cols_t)
    st.download_button("⬇️ 양식 내려받기 (CSV)", sample.to_csv(index=False).encode("utf-8-sig"),
                       file_name="지출_대량등록_양식.csv", mime="text/csv")
    up = st.file_uploader("채운 양식 (.csv / .xlsx)", type=["csv", "xlsx"])
    if up is not None:
        try:
            df = pd.read_excel(up) if up.name.lower().endswith(".xlsx") else pd.read_csv(io.BytesIO(up.getvalue()), encoding="utf-8-sig")
        except Exception as ex:
            st.error(f"파일을 읽지 못했습니다: {ex}"); df = None
        if df is not None:
            df.columns = [str(c).strip() for c in df.columns]
            if not {"항목", "금액"}.issubset(set(df.columns)):
                st.error("필수 열 누락: 항목, 금액 (양식 헤더를 그대로 사용하세요)")
            else:
                rows = []
                for _, r in df.iterrows():
                    try:
                        amt = int(float(str(r.get("금액", 0)).replace(",", "").strip() or 0))
                        if amt <= 0:
                            continue
                        g = str(r.get("구분", "") or "").strip()
                        due_raw = str(r.get("예정일", "") or "").strip()[:10]
                        due = pd.to_datetime(due_raw).date().isoformat() if due_raw else None
                        pv = str(r.get("완료", "") or "").strip().upper() in ("Y", "TRUE", "1", "O", "완료")
                        rows.append({"project_id": bpopts[bpj],
                                     "direction": "in" if g.startswith("받을") or g == "매출" else "out",
                                     "category": str(r.get("항목", "") or "기타").strip(),
                                     "title": str(r.get("제목", "") or "").strip() or None,
                                     "amount": amt, "due_date": due, "paid": pv,
                                     "paid_date": due if pv else None,
                                     "memo": str(r.get("메모", "") or "").strip() or None})
                    except Exception:
                        pass
                st.caption(f"등록 대상 {len(rows)}건")
                if rows and st.button(f"✅ {len(rows)}건 등록", type="primary"):
                    SUPA.table("cash_events").insert(rows).execute()
                    st.toast(f"{len(rows)}건 등록됨 ✓"); st.rerun()

with act[2].popover("🧾 세금계산서 업로드", use_container_width=True):
    st.caption("홈택스 '매출 전자세금계산서 목록' 파일(.xls/.csv)을 그대로 올리세요. "
               "발행분을 '받을' 일정과 금액·날짜로 자동 매칭해 '발행완료'로 표시합니다.")
    tup = st.file_uploader("홈택스 세금계산서 목록 (.xls / .csv)", type=["xls", "csv"], key="tax_up")
    if tup is not None:
        try:
            if tup.name.lower().endswith(".csv"):
                import csv as _csv
                text = tup.getvalue().decode("utf-8-sig", errors="ignore")
                rowdata = list(_csv.reader(io.StringIO(text)))
            else:
                xdf = pd.read_excel(tup, engine="xlrd", header=None)  # xlrd 필요(.xls)
                rowdata = xdf.values.tolist()
        except Exception as ex:
            st.error(f"파일을 읽지 못했습니다: {ex}. (.xls는 requirements에 xlrd 필요)"); rowdata = None
        if rowdata:
            invs = parse_hometax(rowdata)
            pos = [v for v in invs if (v.get("total_amount") or 0) > 0]
            st.caption(f"파싱 {len(invs)}건 (발행분 {len(pos)}건 · 수정/취소 제외)")
            if invs:
                st.dataframe(pd.DataFrame([{
                    "작성일": v.get("write_date"), "상호": v.get("buyer_name"),
                    "등록번호": v.get("buyer_biz_no"), "합계": won(v.get("total_amount")),
                    "구분": v.get("kind")} for v in invs]), use_container_width=True, hide_index=True)
                if st.button("📥 저장 + 자동 매칭", type="primary"):
                    payload = [{
                        "approval_no": v["approval_no"], "write_date": v.get("write_date"),
                        "issue_date": v.get("issue_date"), "buyer_biz_no": v.get("buyer_biz_no"),
                        "buyer_name": v.get("buyer_name"), "total_amount": v.get("total_amount"),
                        "supply_amount": v.get("supply_amount"), "vat": v.get("vat"),
                        "kind": v.get("kind"), "raw": v.get("raw")} for v in invs]
                    SUPA.table("tax_invoices").upsert(payload, on_conflict="approval_no").execute()
                    cp = reconcile_cancellations()
                    n = auto_match_tax()
                    st.success(f"세금계산서 {len(payload)}건 저장 · 취소 짝 {cp}쌍 정리 · '받을' 일정 {n}건 자동 발행완료 ✓")
                    st.toast("저장·정리·매칭 완료"); st.rerun()

with act[3].popover("🏦 계좌 거래내역 업로드", use_container_width=True):
    st.caption("은행에서 그냥 다운받은 거래내역 파일을 그대로 올리세요. 금액 크기는 필터링하지 않습니다 "
               "(소액 해외 인플루언서 송금도 그대로 들어와요). "
               f"**{'·'.join(STAFF_PAYROLL_NAMES)} 이름이 적힌 급여성 거래**와 **이미 등록된 중복**만 자동으로 제외 체크됩니다. "
               "프로젝트 연결 없이 '은행거래' 항목으로 일괄 등록됩니다.")
    bank_up = st.file_uploader("계좌 거래내역 (.csv / .xlsx)", type=["csv", "xlsx"], key="bank_up")
    if bank_up is not None:
        try:
            bdf = pd.read_excel(bank_up) if bank_up.name.lower().endswith(".xlsx") \
                else pd.read_csv(io.BytesIO(bank_up.getvalue()), encoding="utf-8-sig")
            bdf.columns = [str(c).strip() for c in bdf.columns]
        except Exception as ex:
            st.error(f"파일을 읽지 못했습니다: {ex}"); bdf = None
        if bdf is not None:
            parsed, perr = parse_bank_rows(bdf)
            if perr:
                st.error(perr)
            elif not parsed:
                st.warning("입금/출금 금액이 있는 행을 찾지 못했습니다.")
            else:
                parsed = dedup_against_existing(parsed, events_all)
                st.caption(f"총 {len(parsed)}건 인식됨")
                sel = []
                for i, r in enumerate(parsed):
                    reasons = []
                    if looks_like_payroll(r["desc"]):
                        reasons.append("직원 급여로 추정 — 절대 가져오지 않음")
                    if r["_dup_existing"]:
                        reasons.append("이미 등록된 것과 중복")
                    if r["_dup_batch"]:
                        reasons.append("이 파일 안에서 중복")
                    default_check = len(reasons) == 0
                    c1, c2 = st.columns([0.5, 5.5])
                    checked = c1.checkbox("포함", value=default_check, key=f"bankrow_{i}", label_visibility="collapsed")
                    tag = "받을" if r["direction"] == "in" else "나갈"
                    reason_txt = f" — ⚠️ {' · '.join(reasons)}" if reasons else ""
                    c2.write(f"{r['due_date'] or '날짜불명'} · {tag} · {won(r['amount'])} · {r['desc']}{reason_txt}")
                    sel.append(checked)
                n_keep = sum(sel)
                if st.button(f"✅ 체크된 {n_keep}건 등록", type="primary", disabled=n_keep == 0):
                    rows_to_add = []
                    for keep, r in zip(sel, parsed):
                        if not keep:
                            continue
                        rows_to_add.append({
                            "project_id": None, "direction": r["direction"], "category": "은행거래",
                            "title": r["desc"] or None, "amount": r["amount"], "due_date": r["due_date"],
                            "paid": True, "paid_date": r["due_date"],
                        })
                    SUPA.table("cash_events").insert(rows_to_add).execute()
                    st.toast(f"{len(rows_to_add)}건 등록됨 ✓"); st.rerun()

# ── 기간/상태/프로젝트 필터 적용 ──────────────────────────────
def _in_period(e):
    dd = (e.get("due_date") or "")[:10]
    if not dd:
        return True
    try:
        d = date.fromisoformat(dd)
    except Exception:
        return True
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True

def _keep(e):
    if flt == "미입금": ok = e["direction"] == "in" and not e["paid"]
    elif flt == "입금완료": ok = e["direction"] == "in" and e["paid"]
    elif flt == "미지급": ok = e["direction"] == "out" and not e["paid"]
    else: ok = True
    if projsel == "(프로젝트 없음)": ok = ok and (e.get("project_id") is None)
    elif projsel != "전체 프로젝트": ok = ok and (e.get("project_id") == label2id.get(projsel))
    return ok

events = [e for e in events_all if _in_period(e) and _keep(e)]

if not events_all:
    st.info("등록된 송금 일정이 없습니다. 위 '➕ 일정 추가'로 입력하세요."); st.stop()

# ── 수정 팝업(다이얼로그) ─────────────────────────────────────
@st.dialog("내역 수정")
def edit_dialog(e):
    c = st.columns(2)
    dirv = c[0].selectbox("구분", ["받을 돈(매출)", "나갈 돈(매입)"], index=0 if e["direction"] == "in" else 1)
    catv = c[1].selectbox("항목", CATS, index=CATS.index(e["category"]) if e.get("category") in CATS else 4)
    c2 = st.columns(2)
    amtv = c2[0].number_input("금액", min_value=0, value=int(e["amount"] or 0), step=100000)
    try:
        dv = date.fromisoformat((e.get("due_date") or "")[:10])
    except Exception:
        dv = date.today()
    duev = c2[1].date_input("예정일", value=dv)
    titv = st.text_input("제목/메모", value=e.get("title") or "")
    paidv = st.checkbox("입금/지급 완료", value=bool(e["paid"]))
    taxv = e.get("tax_issued"); taxno = e.get("tax_invoice_no") or ""
    if e["direction"] == "in":
        taxv = st.checkbox("세금계산서 발행완료", value=bool(e.get("tax_issued")))
        taxno = st.text_input("승인번호(선택)", value=e.get("tax_invoice_no") or "")
    b = st.columns([1, 1, 3])
    if b[0].button("저장", type="primary"):
        SUPA.table("cash_events").update({
            "direction": "in" if dirv.startswith("받을") else "out", "category": catv,
            "amount": int(amtv), "due_date": duev.isoformat(), "title": titv, "paid": paidv,
            "paid_date": duev.isoformat() if paidv else None,
            "tax_issued": bool(taxv), "tax_invoice_no": (taxno or None)}).eq("id", e["id"]).execute()
        st.session_state.edit_target = None; st.rerun()
    if b[1].button("삭제"):
        SUPA.table("cash_events").delete().eq("id", e["id"]).execute()
        st.session_state.edit_target = None; st.rerun()

if st.session_state.get("edit_target"):
    edit_dialog(st.session_state["edit_target"])

# ── 메인: 달력 / 리스트 ───────────────────────────────────────
if view == "달력":
    if "cal_y" not in st.session_state:
        st.session_state.cal_y = today.year; st.session_state.cal_m = today.month
    nav = st.columns([1, 2, 1])
    if nav[0].button("◀ 이전 달", use_container_width=True):
        m = st.session_state.cal_m - 1; y = st.session_state.cal_y
        if m < 1: m = 12; y -= 1
        st.session_state.cal_m, st.session_state.cal_y = m, y; st.rerun()
    nav[1].markdown(f"<h3 style='text-align:center;margin:0'>{st.session_state.cal_y}년 {st.session_state.cal_m}월</h3>", unsafe_allow_html=True)
    if nav[2].button("다음 달 ▶", use_container_width=True):
        m = st.session_state.cal_m + 1; y = st.session_state.cal_y
        if m > 12: m = 1; y += 1
        st.session_state.cal_m, st.session_state.cal_y = m, y; st.rerun()

    Y, M = st.session_state.cal_y, st.session_state.cal_m
    by_date = {}
    for e in events:
        by_date.setdefault((e.get("due_date") or "")[:10], []).append(e)

    head = st.columns(7)
    for i, wd in enumerate(["일", "월", "화", "수", "목", "금", "토"]):
        head[i].markdown(f"<div style='text-align:center;color:#888;font-size:12px'>{wd}</div>", unsafe_allow_html=True)
    cal = calendar.Calendar(firstweekday=6)
    for week in cal.monthdayscalendar(Y, M):
        cols = st.columns(7)
        for i, day in enumerate(week):
            if day == 0:
                cols[i].markdown("&nbsp;", unsafe_allow_html=True); continue
            iso = date(Y, M, day).isoformat()
            html = "<div style='border:1px solid #eee;border-radius:6px;padding:4px;min-height:64px'>"
            html += f"<div style='font-weight:600;font-size:12px'>{day}</div>"
            for e in by_date.get(iso, []):
                col = "#1aa179" if e["direction"] == "in" else "#d4351c"
                sign = "+" if e["direction"] == "in" else "−"
                chk = "✓" if e["paid"] else ""
                html += f"<div style='font-size:10.5px;color:{col}'>{sign}{won_short(e['amount'])}{chk}</div>"
            html += "</div>"
            cols[i].markdown(html, unsafe_allow_html=True)
    me = [e for e in events if (e.get("due_date") or "")[:7] == f"{Y}-{M:02d}"]
    mi = sum(e["amount"] or 0 for e in me if e["direction"] == "in")
    mo = sum(e["amount"] or 0 for e in me if e["direction"] == "out")
    st.caption(f"이 달 합계 — 받을 {won(mi)} · 나갈 {won(mo)} · 순 {won(mi - mo)}  ·  초록=받을 / 빨강=나갈 / ✓=완료")

elif view == "리스트":  # 리스트
    st.caption(f"{period} · {projsel} · {flt} · {len(events)}건 (오른쪽 '수정'으로 편집·완료·삭제)")
    h = st.columns([1.0, 0.7, 1.9, 1.4, 1.1, 0.7, 1.3, 0.7])
    for i, t in enumerate(["예정일", "구분", "프로젝트", "항목/제목", "금액", "입금", "세금계산서", ""]):
        h[i].markdown(f"<div style='color:#888;font-size:12px'>{t}</div>", unsafe_allow_html=True)
    for e in sorted(events, key=lambda x: (x.get("due_date") or "")):
        c = st.columns([1.0, 0.7, 1.9, 1.4, 1.1, 0.7, 1.3, 0.7])
        c[0].write((e.get("due_date") or "-")[:10])
        c[1].write("받을" if e["direction"] == "in" else "나갈")
        c[2].write(plabel.get(e.get("project_id"), "-"))
        c[3].write(f"{e.get('category') or ''} {('· ' + e['title']) if e.get('title') else ''}")
        c[4].write(won(e["amount"]))
        c[5].write("✅" if e["paid"] else "⏳")
        # 세금계산서: '받을'만 표시 (매출 발행)
        if e["direction"] != "in":
            c[6].write("—")
        elif e.get("tax_issued"):
            c[6].markdown("🧾 발행완료")
        else:
            urgent = False
            dd = (e.get("due_date") or "")[:10]
            if dd:
                try:
                    urgent = (date.fromisoformat(dd) - today).days <= 7
                except Exception:
                    urgent = False
            c[6].markdown("🚨 **발행하세요**" if urgent else "⚠️ 미발행")
        if c[7].button("수정", key="ed" + e["id"]):
            st.session_state.edit_target = e; st.rerun()

    # 미발행 긴급 요약 (받을 · 7일 이내/경과 · 미발행)
    urgent_list = []
    for e in events:
        if e["direction"] == "in" and not e.get("tax_issued") and (e.get("due_date") or ""):
            try:
                if (date.fromisoformat(e["due_date"][:10]) - today).days <= 7:
                    urgent_list.append(e)
            except Exception:
                pass
    if urgent_list:
        st.error("🚨 세금계산서 발행 긴급 — 입금 예정 7일 이내인데 미발행: " +
                 ", ".join(f"{plabel.get(e.get('project_id'),'-')} {won(e['amount'])}" for e in urgent_list[:6])
                 + (" 외" if len(urgent_list) > 6 else ""))

elif view == "세금계산서":
    st.caption("홈택스에서 가져온 세금계산서를 '받을' 일정에 정확히 연결합니다. 금액·날짜가 가까운 후보가 위에 오게 정렬돼요.")
    tinv = SUPA.table("tax_invoices").select("*").order("write_date", desc=True).execute().data
    if not tinv:
        st.info("업로드된 세금계산서가 없습니다. 위 '🧾 세금계산서 업로드'로 먼저 올리세요.")
    else:
        rcv = [e for e in events_all if e["direction"] == "in"]
        ce_by_id = {e["id"]: e for e in events_all}

        def cand_key(inv, e):
            amt_match = 0 if e["amount"] in (inv.get("total_amount"), inv.get("supply_amount")) else 1
            dd = (e.get("due_date") or "")[:10]
            try:
                gap = abs((date.fromisoformat(dd) - date.fromisoformat(inv["write_date"])).days) if (dd and inv.get("write_date")) else 999
            except Exception:
                gap = 999
            return (amt_match, gap)

        unmatched = [v for v in tinv if not v.get("matched_cash_event_id")
                     and (v.get("total_amount") or 0) > 0 and not v.get("canceled")]
        matched = [v for v in tinv if v.get("matched_cash_event_id")]
        canceled = [v for v in tinv if v.get("canceled")]

        rc = st.columns([3, 1])
        rc[0].markdown(f"**미연결 세금계산서 {len(unmatched)}건** — 후보를 고르고 '연결'")
        if rc[1].button("🔄 취소분 다시 정리"):
            cp = reconcile_cancellations()
            st.toast(f"취소 짝 {cp}쌍 정리됨 ✓"); st.rerun()
        for v in unmatched:
            c = st.columns([2.6, 3.0, 0.8])
            c[0].markdown(f"🧾 {v.get('write_date')} · **{v.get('buyer_name') or v.get('buyer_biz_no')}** · {won(v.get('total_amount'))}")
            cands = sorted(rcv, key=lambda e: cand_key(v, e))
            opts = {}
            for e in cands[:25]:
                tag = " ·🧾연결됨" if e.get("tax_issued") else ""
                opts[f"{plabel.get(e.get('project_id'),'(프로젝트없음)')} · {(e.get('due_date') or '-')[:10]} · {won(e['amount'])}{tag}"] = e["id"]
            opts["(연결 안 함)"] = None
            pick = c[1].selectbox("연결할 받을 일정", list(opts.keys()), key="pk" + v["approval_no"], label_visibility="collapsed")
            if c[2].button("연결", key="lk" + v["approval_no"], type="primary"):
                ceid = opts[pick]
                if ceid:
                    SUPA.table("cash_events").update({"tax_issued": True, "tax_invoice_no": v["approval_no"]}).eq("id", ceid).execute()
                    SUPA.table("tax_invoices").update({"matched_cash_event_id": ceid}).eq("approval_no", v["approval_no"]).execute()
                    st.toast("연결됨 ✓"); st.rerun()

        if matched:
            st.divider()
            st.markdown(f"**연결 완료 {len(matched)}건**")
            for v in matched:
                e = ce_by_id.get(v.get("matched_cash_event_id"))
                c = st.columns([2.6, 3.0, 0.8])
                c[0].markdown(f"🧾 {v.get('write_date')} · {v.get('buyer_name') or ''} · {won(v.get('total_amount'))}")
                c[1].write(f"→ {plabel.get(e.get('project_id'),'-') if e else '?'} · {((e.get('due_date') or '-')[:10]) if e else ''} · {won(e['amount']) if e else ''}")
                if c[2].button("해제", key="ul" + v["approval_no"]):
                    if e:
                        SUPA.table("cash_events").update({"tax_issued": False, "tax_invoice_no": None}).eq("id", e["id"]).execute()
                    SUPA.table("tax_invoices").update({"matched_cash_event_id": None}).eq("approval_no", v["approval_no"]).execute()
                    st.toast("연결 해제됨 ✓"); st.rerun()

        neg_unpaired = [v for v in tinv if (v.get("total_amount") or 0) < 0 and not v.get("canceled")]
        if canceled:
            with st.expander(f"🚫 취소(상쇄) 처리된 세금계산서 {len(canceled)}건 — 매칭 대상 제외"):
                for v in sorted(canceled, key=lambda x: x.get("write_date") or ""):
                    st.caption(f"{v.get('write_date')} · {v.get('buyer_name') or ''} · {won(v.get('total_amount'))} ({v.get('kind')})")
        if neg_unpaired:
            st.warning(f"⚠️ 짝을 못 찾은 수정/취소(음수) {len(neg_unpaired)}건 — 원본 금액·거래처가 다르면 수동 확인 필요. "
                       "'🔄 취소분 다시 정리'를 눌러보세요.")

# ── 하단 요약 (수익률) ────────────────────────────────────────
if view in ("달력", "리스트"):
    st.divider()
    i_s = sum(e["amount"] or 0 for e in events if e["direction"] == "in")
    o_s = sum(e["amount"] or 0 for e in events if e["direction"] == "out")
    rate = round((i_s - o_s) / i_s * 100, 1) if i_s else 0
    s = st.columns(4)
    s[0].metric("받을 합계", won(i_s))
    s[1].metric("나갈 합계", won(o_s))
    s[2].metric("순액 (받을 − 나갈)", won(i_s - o_s))
    s[3].metric("수익률", f"{rate}%")
