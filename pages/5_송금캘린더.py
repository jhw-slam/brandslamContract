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

def load_bank_txns():
    return SUPA.table("bank_transactions").select("*").order("txn_date").execute().data

def matched_txns_for(event_id, bank_txns):
    return [t for t in bank_txns if t.get("matched_cash_event_id") == event_id]

def matched_sum_for(event_id, bank_txns):
    return sum(t["amount"] or 0 for t in matched_txns_for(event_id, bank_txns))

def suggest_txn_candidates(event, bank_txns, limit=15):
    """시점(예정일)·잔여금액 기준으로 후보 계좌거래를 추천 정렬한다."""
    try:
        due = date.fromisoformat((event.get("due_date") or "")[:10])
    except Exception:
        due = None
    remaining = (event["amount"] or 0) - matched_sum_for(event["id"], bank_txns)
    cands = [t for t in bank_txns if t["direction"] == event["direction"] and not t.get("matched_cash_event_id")]

    def _score(t):
        try:
            gap = abs((date.fromisoformat(t["txn_date"]) - due).days) if (due and t.get("txn_date")) else 999
        except Exception:
            gap = 999
        amt_diff = abs((t["amount"] or 0) - remaining)
        return (gap, amt_diff)

    return sorted(cands, key=_score)[:limit]

# ── 은행 거래내역 파싱 (은행마다 헤더가 달라서 유연하게 컬럼을 찾음) ──
# 급여 지급 대상 — 이 6명 이름이 메모에 포함된 거래는 절대 가져오지 않는다 (인원 변경 시 이 목록만 수정).
STAFF_PAYROLL_NAMES = ["김선재", "정다영", "양혜준", "구정회", "박솔", "장현우"]

_COLKEY = {
    "date": ["거래일시", "거래일자", "거래일", "일자", "날짜", "이용일자", "승인일자"],
    "deposit": ["입금액", "입금", "맡기신금액", "들어온금액"],
    "withdraw": ["출금액", "출금", "찾으신금액", "나간금액", "이용금액"],
    "desc": ["거래내용", "내용", "적요", "거래구분", "받으신분", "보내신분", "가맹점명", "메모", "비고"],
}

def _find_col(columns, keys):
    for col in columns:
        c = re.sub(r"\s+", "", str(col).strip())
        for k in keys:
            if k in c:
                return col
    return None

def looks_like_payroll(desc):
    """메모에 사내 직원 이름이 포함되면 급여로 간주해 무조건 제외 (금액 크기와 무관 — 소액 인플루언서 해외송금은 별개)."""
    s = re.sub(r"\s+", "", (desc or ""))
    return any(name in s for name in STAFF_PAYROLL_NAMES)

def _unescape_x(s):
    """엑셀이 전각 특수문자(－（） 등)를 _xFF0D_ 같은 이스케이프로 저장해두는 경우가 있어 실제 문자로 되돌린다."""
    if not isinstance(s, str):
        return s
    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda m: chr(int(m.group(1), 16)), s)

# 일부 은행 엑셀은 파일 자체에 오타가 있어 openpyxl이 못 읾을 때가 있음
# (예: SC제일은행 — styles.xml에 applyNumberFormat이 applyNumberForm으로 잘못 저장됨).
# 통계/서식 정보는 필요 없고 값만 필요하므로, 문제되는 속성명을 고쳐서 재포장한 뒤 읽는다.
def _fix_xlsx_style_bug(data: bytes) -> bytes:
    try:
        import zipfile
        zin = zipfile.ZipFile(io.BytesIO(data))
        buf = io.BytesIO()
        zout = zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED)
        changed = False
        for item in zin.infolist():
            content = zin.read(item.filename)
            if item.filename == "xl/styles.xml" and b"applyNumberForm=" in content:
                content = content.replace(b"applyNumberForm=", b"applyNumberFormat=")
                changed = True
            zout.writestr(item, content)
        zout.close()
        return buf.getvalue() if changed else data
    except Exception:
        return data

_HEADER_DATE_KW = ("거래일시", "거래일자", "거래일", "일자", "날짜")
_HEADER_AMT_KW = ("찾으신", "맡기신", "출금", "입금")

def _find_table_header_row(raw_df, max_scan=60):
    """은행 엑셀은 위에 계좌정보 몇 줄이 더 있는 경우가 많아, 진짜 표 헤더가 몇 번째 줄인지 자동으로 찾는다."""
    for i in range(min(max_scan, len(raw_df))):
        cells = [str(x) for x in raw_df.iloc[i].tolist() if str(x) != "nan"]
        joined = "".join(cells)
        if any(k in joined for k in _HEADER_DATE_KW) and any(k in joined for k in _HEADER_AMT_KW):
            return i
    return None

def _date_ok(v):
    try:
        return pd.notna(pd.to_datetime(str(v)[:19], errors="coerce"))
    except Exception:
        return False

def _date_count(series):
    return sum(1 for v in series.dropna() if _date_ok(v))

def _numeric_count(series):
    n = 0
    for v in series.dropna():
        try:
            float(str(v).replace(",", "")); n += 1
        except Exception:
            pass
    return n

def _hangul_total(series):
    return sum(len(re.findall(r"[가-힣]", str(v))) for v in series.dropna())

def _guess_headerless_columns(raw_df):
    """헤더 행이 없는 시트(은행이 페이지를 나눠서 첫 페이지만 헤더를 넣는 경우)에서,
    값의 '개수'(비율이 아니라) 기준으로 날짜/입금/출금/설명 컬럼을 추정한다.
    비율 기준으로 하면 값이 거의 없는 컬럼이 우연히 100% 매칭돼 잘못 뽑히는 문제가 있어 개수를 쓴다."""
    cols = list(raw_df.columns)
    if not cols:
        return None
    dcounts = {c: _date_count(raw_df[c]) for c in cols}
    c_date = max(dcounts, key=dcounts.get)
    if dcounts[c_date] < 3:
        return None
    numeric_cols = [c for c in cols if c != c_date and _numeric_count(raw_df[c]) >= max(3, len(raw_df) // 4)]
    numeric_cols = sorted(numeric_cols, key=lambda c: cols.index(c))
    if len(numeric_cols) < 2:
        return None
    c_wd, c_dep = numeric_cols[0], numeric_cols[1]
    text_cols = [c for c in cols if c not in (c_date, c_wd, c_dep)]
    text_cols_scored = sorted(text_cols, key=lambda c: -_hangul_total(raw_df[c]))
    c_desc = text_cols_scored[0] if text_cols_scored else None
    return c_date, c_wd, c_dep, c_desc

def _extract_rows_from_sheet(df, c_date, c_wd, c_dep, c_desc):
    out = []
    for _, r in df.iterrows():
        dv = r.get(c_date)
        if not _date_ok(dv):
            continue  # 요약/합계/안내문 등 거래가 아닌 줄은 날짜가 없어 자동으로 걸러짐
        wd = 0.0
        dep = 0.0
        try:
            wd = float(str(r.get(c_wd, 0)).replace(",", "")) if c_wd is not None else 0.0
        except Exception:
            pass
        try:
            dep = float(str(r.get(c_dep, 0)).replace(",", "")) if c_dep is not None else 0.0
        except Exception:
            pass
        if wd == 0 and dep == 0:
            continue
        direction, amount = ("out", int(abs(wd))) if wd > dep else ("in", int(abs(dep)))
        d = pd.to_datetime(str(dv)[:19], errors="coerce")
        due = d.date().isoformat() if pd.notna(d) else None
        desc = _unescape_x(str(r.get(c_desc, "")).strip()) if c_desc is not None else ""
        out.append({"direction": direction, "amount": amount, "due_date": due, "desc": desc})
    return out

def read_and_parse_bank_file(uploaded_file):
    """csv/xlsx를 읽어 거래 목록을 반환한다. xlsx는 시트가 여러 개로 나뉜 경우(은행이 페이지별로
    별도 시트에 저장하는 경우가 흔함) 전부 순회하며, 각 시트마다 헤더가 있으면 헤더로, 없으면
    값 패턴으로 컬럼을 추정해 최대한 다 긁어온다."""
    name = uploaded_file.name.lower()
    all_rows = []
    sheet_info = []
    if name.endswith(".xlsx"):
        data = _fix_xlsx_style_bug(uploaded_file.getvalue())
        xls = pd.ExcelFile(io.BytesIO(data))
        for sheetname in xls.sheet_names:
            raw = pd.read_excel(xls, sheet_name=sheetname, header=None)
            hidx = _find_table_header_row(raw)
            if hidx is not None:
                header = [str(x).strip() if str(x) != "nan" else f"col_{i}" for i, x in enumerate(raw.iloc[hidx].tolist())]
                df = raw.iloc[hidx + 1:].copy()
                df.columns = header
                c_date = _find_col(df.columns, _COLKEY["date"])
                c_dep = _find_col(df.columns, _COLKEY["deposit"])
                c_wd = _find_col(df.columns, _COLKEY["withdraw"])
                c_desc = _find_col(df.columns, _COLKEY["desc"])
                if c_date is None or (c_dep is None and c_wd is None):
                    guess = _guess_headerless_columns(df)
                    if guess:
                        c_date, c_wd, c_dep, c_desc = guess
            else:
                guess = _guess_headerless_columns(raw)
                if not guess:
                    continue
                c_date, c_wd, c_dep, c_desc = guess
                df = raw
            if c_date is None or (c_wd is None and c_dep is None):
                continue
            rows = _extract_rows_from_sheet(df, c_date, c_wd, c_dep, c_desc)
            if rows:
                all_rows.extend(rows)
                sheet_info.append(f"{sheetname}({len(rows)}건)")
    else:
        raw = pd.read_csv(io.BytesIO(uploaded_file.getvalue()), encoding="utf-8-sig", header=None)
        hidx = _find_table_header_row(raw)
        if hidx is not None:
            header = [str(x).strip() if str(x) != "nan" else f"col_{i}" for i, x in enumerate(raw.iloc[hidx].tolist())]
            df = raw.iloc[hidx + 1:].copy(); df.columns = header
        else:
            df = raw.copy(); df.columns = [str(c).strip() for c in raw.iloc[0].tolist()]; df = df.iloc[1:]
        c_date = _find_col(df.columns, _COLKEY["date"])
        c_dep = _find_col(df.columns, _COLKEY["deposit"])
        c_wd = _find_col(df.columns, _COLKEY["withdraw"])
        c_desc = _find_col(df.columns, _COLKEY["desc"])
        if c_date is not None and (c_dep is not None or c_wd is not None):
            all_rows = _extract_rows_from_sheet(df, c_date, c_wd, c_dep, c_desc)
            sheet_info = [f"CSV({len(all_rows)}건)"]

    if not all_rows:
        return None, "거래 데이터를 찾지 못했습니다. 파일 형식을 확인해주세요."
    return all_rows, " · ".join(sheet_info)

def dedup_against_existing(rows, existing_bank_txns, account_label):
    seen_existing = {(t.get("account_label") or "", t["direction"], t.get("amount") or 0, (t.get("txn_date") or "")[:10])
                      for t in existing_bank_txns}
    seen_batch = set()
    for r in rows:
        key = (account_label, r["direction"], r["amount"], r["due_date"] or "")
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
bank_txns_all = load_bank_txns()

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
act = st.columns([1.1, 1.8, 1.8, 2.2])
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

with act[1].popover("🧾 세금계산서 업로드", use_container_width=True):
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

with act[2].popover("🏦 계좌 거래내역 업로드", use_container_width=True):
    st.caption("은행에서 그냥 다운받은 거래내역 파일을 그대로 올리세요. 금액 크기는 필터링하지 않습니다 "
               "(소액 해외 송금도 그대로 들어와요). 사내 정책상 일부 거래와 중복 건은 자동으로 제외 체크됩니다. "
               "프로젝트 연결 없이 '은행거래' 항목으로 일괄 등록됩니다.")
    existing_accounts = sorted({t.get("account_label") for t in bank_txns_all if t.get("account_label")})
    acc_opts = existing_accounts + ["+ 새 계좌 이름 직접 입력"]
    acc_pick = st.selectbox("이 파일은 어느 계좌 것인가요? (계좌별로 구분해야 중복 체크가 정확해요)", acc_opts)
    account_label = st.text_input("계좌 이름 입력", placeholder="예: 국민은행 법인, 카카오뱅크") \
        if acc_pick == "+ 새 계좌 이름 직접 입력" else acc_pick
    bank_up = st.file_uploader("계좌 거래내역 (.csv / .xlsx)", type=["csv", "xlsx"], key="bank_up")
    if bank_up is not None and not account_label.strip():
        st.warning("계좌 이름을 먼저 입력해주세요.")
    elif bank_up is not None:
        try:
            parsed, perr_or_info = read_and_parse_bank_file(bank_up)
        except Exception as ex:
            parsed, perr_or_info = None, f"파일을 읽지 못했습니다: {ex}"
        if parsed is None:
            st.error(perr_or_info)
        else:
            st.caption(f"시트별 인식 현황: {perr_or_info}")
            if not parsed:
                st.warning("입금/출금 금액이 있는 행을 찾지 못했습니다.")
            else:
                parsed = dedup_against_existing(parsed, bank_txns_all, account_label.strip())
                st.caption(f"**{account_label.strip()}** · 총 {len(parsed)}건 인식됨")
                sel = []
                for i, r in enumerate(parsed):
                    reasons = []
                    if looks_like_payroll(r["desc"]):
                        reasons.append("제외 대상")
                    if r["_dup_existing"]:
                        reasons.append("이미 등록된 것과 중복")
                    if r["_dup_batch"]:
                        reasons.append("이 파일 안에서 중복")
                    default_check = len(reasons) == 0
                    c1, c2 = st.columns([0.5, 5.5])
                    checked = c1.checkbox("포함", value=default_check, key=f"bankrow_{i}", label_visibility="collapsed")
                    tag = "받을" if r["direction"] == "in" else "나갈"
                    reason_txt = f" — {' · '.join(reasons)}" if reasons else ""
                    c2.write(f"{r['due_date'] or '날짜불명'} · {tag} · {won(r['amount'])} · {r['desc']}{reason_txt}")
                    sel.append(checked)
                n_keep = sum(sel)
                st.caption("등록 후 '리스트' 보기에서 미입금/미지급 항목을 열면 이 거래들을 매칭할 수 있어요.")
                if st.button(f"✅ 체크된 {n_keep}건 등록", type="primary", disabled=n_keep == 0):
                    rows_to_add = []
                    for keep, r in zip(sel, parsed):
                        if not keep:
                            continue
                        rows_to_add.append({
                            "direction": r["direction"], "amount": r["amount"],
                            "txn_date": r["due_date"], "description": r["desc"] or None,
                            "account_label": account_label.strip(),
                        })
                    SUPA.table("bank_transactions").insert(rows_to_add).execute()
                    st.toast(f"{len(rows_to_add)}건 등록됨 ✓ — 이제 '리스트'에서 매칭해주세요"); st.rerun()

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
    st.caption(f"{period} · {projsel} · {flt} · {len(events)}건 (오른쪽 '수정'으로 편집·완료·삭제, '매칭'으로 계좌거래 연결)")
    h = st.columns([1.0, 0.7, 1.9, 1.4, 1.1, 1.1, 1.3, 0.7])
    for i, t in enumerate(["예정일", "구분", "프로젝트", "항목/제목", "금액", "입금", "세금계산서", ""]):
        h[i].markdown(f"<div style='color:#888;font-size:12px'>{t}</div>", unsafe_allow_html=True)
    for e in sorted(events, key=lambda x: (x.get("due_date") or "")):
        received = matched_sum_for(e["id"], bank_txns_all)
        c = st.columns([1.0, 0.7, 1.9, 1.4, 1.1, 1.1, 1.3, 0.7])
        c[0].write((e.get("due_date") or "-")[:10])
        c[1].write("받을" if e["direction"] == "in" else "나갈")
        c[2].write(plabel.get(e.get("project_id"), "-"))
        c[3].write(f"{e.get('category') or ''} {('· ' + e['title']) if e.get('title') else ''}")
        c[4].write(won(e["amount"]))
        if e["paid"]:
            c[5].write("✅ 완료")
        elif received > 0:
            c[5].markdown(f"🟡 {won_short(received)}/{won_short(e['amount'])}")
        else:
            c[5].write("⏳")
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

        if not e["paid"]:
            with st.expander(f"🔗 매칭 — {plabel.get(e.get('project_id'),'(프로젝트없음)')} · 잔여 {won(e['amount'] - received)}"):
                already = matched_txns_for(e["id"], bank_txns_all)
                if already:
                    st.caption("이미 매칭된 계좌거래")
                    for t in already:
                        mc = st.columns([4, 1])
                        mc[0].write(f"[{t.get('account_label') or '계좌미표시'}] {t.get('txn_date') or '-'} · {won(t['amount'])} · {t.get('description') or ''}")
                        if mc[1].button("해제", key="unlk" + t["id"]):
                            SUPA.table("bank_transactions").update({"matched_cash_event_id": None}).eq("id", t["id"]).execute()
                            st.rerun()
                    st.divider()
                cands = suggest_txn_candidates(e, bank_txns_all)
                if not cands:
                    st.caption("매칭 후보가 될 미매칭 계좌거래가 없습니다. 위 '🏦 계좌 거래내역 업로드'로 먼저 올려주세요.")
                else:
                    st.caption("예정일에 가깝고 잔여금액과 비슷한 순서로 추천했어요. 여러 건을 함께 체크해 나눠 받은 금액도 합칠 수 있습니다.")
                    picks = []
                    running = 0
                    for j, t in enumerate(cands):
                        pc = st.columns([0.5, 4.5])
                        chk = pc[0].checkbox("선택", value=False, key=f"pick_{e['id']}_{j}", label_visibility="collapsed")
                        pc[1].write(f"[{t.get('account_label') or '계좌미표시'}] {t.get('txn_date') or '날짜불명'} · {won(t['amount'])} · {t.get('description') or ''}")
                        if chk:
                            picks.append(t); running += t["amount"] or 0
                    st.caption(f"선택 합계: {won(running)}  (잔여 {won(e['amount'] - received)} 대비)")
                    if st.button(f"✅ 선택한 {len(picks)}건 매칭", key="matchbtn" + e["id"], disabled=not picks):
                        for t in picks:
                            SUPA.table("bank_transactions").update({"matched_cash_event_id": e["id"]}).eq("id", t["id"]).execute()
                        new_total = received + running
                        if new_total >= (e["amount"] or 0):
                            last_date = max((t.get("txn_date") for t in picks if t.get("txn_date")), default=e.get("due_date"))
                            SUPA.table("cash_events").update({"paid": True, "paid_date": last_date}).eq("id", e["id"]).execute()
                            st.toast("매칭 완료 — 목표 금액을 채워 '완료'로 표시됩니다 ✓")
                        else:
                            st.toast(f"매칭됨 ✓ (아직 {won(e['amount'] - new_total)} 남음)")
                        st.rerun()

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

    unmatched_txns = [t for t in bank_txns_all if not t.get("matched_cash_event_id")]
    if unmatched_txns:
        with st.expander(f"📎 어디에도 매칭 안 된 계좌거래 {len(unmatched_txns)}건 — 예정된 항목이 없던 거래"):
            st.caption("위 '🔗 매칭'에 연결할 데가 없으면, 여기서 개별로 새 항목을 만들 수 있어요.")
            for t in sorted(unmatched_txns, key=lambda x: x.get("txn_date") or "", reverse=True):
                uc = st.columns([4, 1])
                tag = "받을" if t["direction"] == "in" else "나갈"
                uc[0].write(f"[{t.get('account_label') or '계좌미표시'}] {t.get('txn_date') or '-'} · {tag} · {won(t['amount'])} · {t.get('description') or ''}")
                if uc[1].button("새 항목으로", key="mkce" + t["id"]):
                    new_ce = SUPA.table("cash_events").insert({
                        "project_id": None, "direction": t["direction"], "category": "은행거래",
                        "title": t.get("description"), "amount": t["amount"], "due_date": t.get("txn_date"),
                        "paid": True, "paid_date": t.get("txn_date"),
                    }).execute().data[0]
                    SUPA.table("bank_transactions").update({"matched_cash_event_id": new_ce["id"]}).eq("id", t["id"]).execute()
                    st.toast("새 항목으로 등록됨 ✓"); st.rerun()

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
