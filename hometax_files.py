"""홈택스에서 받은 세금계산서 파일(목록 엑셀 · 인쇄 PDF)을 읽어 '무슨 파일인지' 확인해 주는 계산. streamlit 에 의존하지 않는다.

목적은 저장이 아니라 **올리기 전 확인**이다: 매출/매입 구분, 기간, 건수, 합계, 우리 사업자번호가 맞는지.
열 위치를 고정하지 않고(시트마다 다름) 셀 내용의 모양(날짜·승인번호·사업자번호·숫자)으로 읽는다.
"""
import io
import re
from datetime import date

import pandas as pd

BIZNO = re.compile(r"^\d{3}-\d{2}-\d{5}$")
APPROVAL = re.compile(r"^\d{8}-\d{8}-\d{8}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NUM = re.compile(r"^-?[\d,]+(\.\d+)?$")


def _num(v):
    s = str(v).strip().replace("−", "-")
    if not NUM.match(s):
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _cells(row):
    return [str(c).strip() for c in row if not (c is None or (isinstance(c, float) and pd.isna(c))) and str(c).strip() != ""]


def parse_hometax_list(data, filename=""):
    """홈택스 '전자세금계산서 목록' 파일(.xls/.xlsx/.csv). 한 계산서가 두 줄(윗줄: 번호·작성일자·승인번호·상대 사업자번호·상호·대표자·종류 /
    아랫줄: 발급일자·전송일자·합계·공급가액·세액·발급유형)로 나오는 형식을 읽는다.
    반환: {"kind": "매출"|"매입"|None, "period": (시작, 끝)|None, "rows": [...], "supply": 합, "vat": 합, "sheets": n} 또는 {"error": 문구}"""
    name = (filename or "").lower()
    try:
        if name.endswith(".csv"):
            sheets = {"csv": pd.read_csv(io.BytesIO(data), header=None, dtype=str, encoding_errors="replace")}
        else:
            sheets = pd.read_excel(io.BytesIO(data), sheet_name=None, header=None, dtype=object)
    except Exception as e:
        return {"error": f"파일을 열지 못했어요 ({type(e).__name__}: {str(e)[:100]}). 홈택스에서 받은 .xls/.xlsx/.csv 원본인지 확인해주세요."}
    kind, period, rows = None, None, []
    for _, sh in sheets.items():
        lines = [_cells(r) for r in sh.values.tolist()]
        for cells in lines:
            joined = " ".join(cells)
            if kind is None and "전자세금계산서 목록" in joined:
                kind = "매출" if "매출" in joined else ("매입" if "매입" in joined else None)
            if kind is None and "계산서 목록" in joined:
                kind = "매출" if "매출" in joined else ("매입" if "매입" in joined else None)
            m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*[∼~\-]\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", joined)
            if m and period is None:
                a = list(map(int, m.groups()))
                period = (date(a[0], a[1], a[2]), date(a[3], a[4], a[5]))
        i = 0
        while i < len(lines):
            cells = lines[i]
            appr = next((c for c in cells if APPROVAL.match(c)), None)
            if appr and i + 1 < len(lines):
                dts = [c for c in cells if DATE.match(c)]
                biz = next((c for c in cells if BIZNO.match(c)), None)
                texts = [c for c in cells if not (DATE.match(c) or APPROVAL.match(c) or BIZNO.match(c) or _num(c) is not None)]
                nxt = lines[i + 1]
                nums = [n for n in (_num(c) for c in nxt if not DATE.match(c)) if n is not None]
                ndts = [c for c in nxt if DATE.match(c)]
                if len(nums) >= 3:
                    rows.append({
                        "approval_no": appr, "write_date": dts[0] if dts else None, "issue_date": ndts[0] if ndts else None,
                        "biz_no": biz, "name": texts[0] if texts else "", "rep": texts[1] if len(texts) > 1 else "",
                        "kind": texts[2] if len(texts) > 2 else "", "total": nums[0], "supply": nums[1], "vat": nums[2],
                    })
                    i += 2
                    continue
            i += 1
    if not rows:
        return {"error": "계산서 행을 찾지 못했어요. 홈택스 '전자세금계산서 목록 조회'에서 받은 엑셀(승인번호가 있는 목록)인지 확인해주세요."}
    return {"kind": kind, "period": period, "rows": rows, "supply": sum(r["supply"] for r in rows), "vat": sum(r["vat"] for r in rows),
            "sheets": len(sheets)}


def parse_invoice_pdf(data):
    """홈택스 '전자세금계산서 인쇄' PDF(한 쪽에 여러 장 가능). 반환: {"invoices": [...]} 또는 {"error": 문구}"""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as e:
        return {"error": f"PDF를 읽지 못했어요 ({type(e).__name__}: {str(e)[:100]})"}
    blocks = re.split(r"전자세금계산서\s*승인번호\s*", text)[1:]
    invs = []
    for b in blocks:
        ap = re.match(r"(\d{8}-\d{8}-\d{8})", b)
        if not ap:
            continue
        biz = re.findall(r"(\d{3}-\d{2}-\d{5})", b)
        amt = re.search(r"(\d{4}-\d{2}-\d{2})\s+([\d,]+)\s+([\d,]+)\s+\S*", b)
        names = re.findall(r"상호\s*\(법인명\)\s*(.+?)\s+성명", b.replace("\n", " "))
        if not amt:
            continue
        invs.append({"approval_no": ap.group(1), "supplier_biz": biz[0] if biz else None, "buyer_biz": biz[1] if len(biz) > 1 else None,
                     "supplier": names[0].strip() if names else "", "buyer": names[1].strip() if len(names) > 1 else "",
                     "write_date": amt.group(1), "supply": float(amt.group(2).replace(",", "")), "vat": float(amt.group(3).replace(",", ""))})
    if not invs:
        return {"error": "전자세금계산서를 찾지 못했어요. 홈택스에서 '인쇄'로 저장한 전자세금계산서 PDF인지 확인해주세요(스캔 이미지 PDF는 읽을 수 없어요 — 그 경우 그대로 첨부만 하세요)."}
    return {"invoices": invs}


def check_list(parsed, expect_kind, start, end, our_biz=None):
    """올리기 전 점검 메시지. 반환 [(등급, 문구)] 등급: ok/warn/bad"""
    msgs = []
    if parsed.get("error"):
        return [("bad", parsed["error"])]
    if parsed["kind"] and expect_kind and parsed["kind"] != expect_kind:
        msgs.append(("bad", f"이 파일은 '{parsed['kind']}' 목록이에요. 여기에는 '{expect_kind}' 목록을 올려야 해요."))
    p = parsed["period"]
    if p:
        if p[0] > end or p[1] < start:
            msgs.append(("bad", f"파일의 기간({p[0]} ~ {p[1]})이 대상 기간({start} ~ {end})과 겹치지 않아요."))
        elif p[0] > start or p[1] < end:
            msgs.append(("warn", f"파일의 기간({p[0]} ~ {p[1]})이 대상 기간({start} ~ {end})을 다 덮지 못해요. 홈택스에서 기간을 다시 지정해 받아주세요."))
        elif p[0] < start or p[1] > end:
            msgs.append(("warn", f"파일의 기간({p[0]} ~ {p[1]})이 대상 기간보다 넓어요. 대상 기간 밖의 계산서가 섞여 있을 수 있어요."))
    out_of = [r for r in parsed["rows"] if r["write_date"] and not (start.isoformat() <= r["write_date"] <= end.isoformat())]
    if out_of:
        msgs.append(("warn", f"대상 기간 밖(작성일자 기준)의 계산서 {len(out_of)}건이 섞여 있어요."))
    if not msgs:
        msgs.append(("ok", "형식·기간이 맞아 보여요."))
    return msgs


def check_pdf(parsed, start, end, our_biz=None):
    if parsed.get("error"):
        return [("warn", parsed["error"])]
    msgs, invs = [], parsed["invoices"]
    if our_biz:
        others = [i for i in invs if our_biz not in (i["supplier_biz"], i["buyer_biz"])]
        if others:
            names = ", ".join(sorted({(o["supplier"] or o["supplier_biz"] or "?") for o in others})[:3])
            msgs.append(("bad", f"{len(others)}건은 우리 사업자번호({our_biz})가 아닌 계산서예요 (공급자: {names}). 다른 사업자의 파일이 아닌지 확인하세요."))
    out_of = [i for i in invs if not (start.isoformat() <= i["write_date"] <= end.isoformat())]
    if out_of:
        msgs.append(("warn", f"대상 기간({start} ~ {end}) 밖의 계산서 {len(out_of)}건이 있어요 (작성일자 {min(i['write_date'] for i in out_of)} 등)."))
    if not msgs:
        msgs.append(("ok", "우리 사업자번호·기간이 맞아 보여요."))
    return msgs
