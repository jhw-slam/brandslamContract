"""부가세 신고 자료 준비(CFO › VAT Filing)가 쓰는 계산. streamlit 에 의존하지 않는다(테스트 쉬움).

여기서 하는 일은 '자료 정리'까지다. 세액 계산·신고서 작성·홈택스 제출은 세무사사무실 몫이라 하지 않는다.
숫자를 만들어 내지 않는다: 없으면 0건/'올려주세요'로 보여준다.
"""
import io
import re
import zipfile
from datetime import date, timedelta

import pandas as pd

# 구분 → (이름, 시작월, 끝월, 신고기한(월, 일, 연도 오프셋))
# 법인사업자 기준 일반 일정(세무사사무실에 대상 여부·기한 확인 필요)
PERIODS = {
    "1기 예정 (1~3월)": (1, 3, (4, 25, 0)),
    "1기 확정 (4~6월)": (4, 6, (7, 25, 0)),
    "2기 예정 (7~9월)": (7, 9, (10, 25, 0)),
    "2기 확정 (10~12월)": (10, 12, (1, 25, 1)),
}
MASK_TEXT = "(급여·인건비 — 개인명 가림)"


def period_range(year, label):
    m1, m2, _ = PERIODS[label]
    start = date(year, m1, 1)
    end = (date(year + (m2 == 12), (m2 % 12) + 1, 1)) - timedelta(days=1)
    return start, end


def period_deadline(year, label):
    m, d, off = PERIODS[label][2]
    return date(year + off, m, d)


def default_period(today):
    """오늘 기준 '다음 마감'이 가장 가까운 (연도, 구분)."""
    best = None
    for y in (today.year - 1, today.year, today.year + 1):
        for label in PERIODS:
            dl = period_deadline(y, label)
            if dl >= today and (best is None or dl < best[0]):
                best = (dl, y, label)
    return best[1], best[2]


def find_date_gaps(dates, start, end, min_gap_days=7):
    """거래가 하나도 없는 연속 구간(일수가 min_gap_days 이상)을 찾는다. 반환: [(구간시작, 구간끝, 일수, 설명)]
    '의심 구간'일 뿐 — 실제로 거래가 없었을 수도 있다."""
    ds = sorted({d for d in dates if d is not None and start <= d <= end})
    out = []
    if not ds:
        return [(start, end, (end - start).days + 1, "기간 전체에 거래 없음")]
    if (ds[0] - start).days >= min_gap_days:
        out.append((start, ds[0] - timedelta(days=1), (ds[0] - start).days, "기간 시작 후 첫 거래까지"))
    for a, b in zip(ds, ds[1:]):
        if (b - a).days - 1 >= min_gap_days:
            out.append((a + timedelta(days=1), b - timedelta(days=1), (b - a).days - 1, "거래 없는 구간"))
    if (end - ds[-1]).days >= min_gap_days:
        out.append((ds[-1] + timedelta(days=1), end, (end - ds[-1]).days, "마지막 거래 이후 기간 끝까지"))
    return out


def bank_status(row):
    if row.get("is_dup_certain"):
        return "상쇄(제외)"
    if row.get("cat_type") == "transfer":
        return "내부이체"
    if row.get("is_dup_suspect"):
        return "애매(같은 날 같은 금액의 반대 거래)"
    if row.get("cat_name") in (None, "미분류") or pd.isna(row.get("account_category_id")):
        return "미분류"
    return "정상"


def prepare_ledger(df, mask_labor=True):
    """은행 거래 DataFrame(mark_duplicates·분류 컬럼 포함) → 엑셀용 원장. 급여·인건비 적요는 기본으로 가린다."""
    d = df.copy()
    d["상태"] = d.apply(bank_status, axis=1)
    desc = d["description"].fillna("")
    if mask_labor and "cat_type" in d.columns:
        desc = desc.where(d["cat_type"] != "labor", MASK_TEXT)
    ts = d["txn_datetime"] if "txn_datetime" in d.columns else pd.Series(pd.NaT, index=d.index)
    out = pd.DataFrame({
        "날짜": d["txn_date"].dt.strftime("%Y-%m-%d"),
        "시각": pd.to_datetime(ts, errors="coerce").dt.strftime("%H:%M:%S").fillna(""),
        "구분": d["direction"].map({"in": "입금", "out": "출금"}),
        "금액": d["amount"].astype(float),
        "적요": desc,
        "계정과목": d["cat_name"].fillna("미분류"),
        "상태": d["상태"],
    })
    return out.sort_values(["날짜", "시각"], kind="stable").reset_index(drop=True)


def account_summary(ledger, start, end, collected_from=None):
    """요약 시트용 (항목, 값) 목록."""
    inc = ledger[ledger["상태"] != "상쇄(제외)"]
    ins, outs = inc[inc["구분"] == "입금"], inc[inc["구분"] == "출금"]
    off = ledger[ledger["상태"] == "상쇄(제외)"]
    rows = [
        ("기간", f"{start} ~ {end}"),
        ("거래 건수(전체)", len(ledger)),
        ("입금 건수 / 합계 (상쇄 제외)", f"{len(ins):,}건 / ₩{ins['금액'].sum():,.0f}"),
        ("출금 건수 / 합계 (상쇄 제외)", f"{len(outs):,}건 / ₩{outs['금액'].sum():,.0f}"),
        ("상쇄(결제↔취소) 제외 건수", f"{len(off):,}건 (입금 ₩{off[off['구분'] == '입금']['금액'].sum():,.0f} / 출금 ₩{off[off['구분'] == '출금']['금액'].sum():,.0f})"),
        ("내부이체 건수", int((ledger["상태"] == "내부이체").sum())),
        ("애매한 의심 건수", int(ledger["상태"].str.startswith("애매").sum())),
        ("미분류 건수", int((ledger["상태"] == "미분류").sum())),
        ("첫 거래일 / 마지막 거래일", f"{ledger['날짜'].min() if len(ledger) else '-'} / {ledger['날짜'].max() if len(ledger) else '-'}"),
    ]
    if collected_from and collected_from > start:
        rows.append(("⚠️ 주의", f"이 계좌의 수집 데이터는 {collected_from}부터라 그 이전 기간은 비어 있을 수 있어요"))
    rows.append(("메모", "상쇄·내부이체·미분류는 별도 시트에 있고, 데이터는 지우지 않았어요. 최종 판단은 세무사사무실이 합니다."))
    return rows


def monthly_table(ledger):
    inc = ledger[ledger["상태"] != "상쇄(제외)"].copy()
    inc["월"] = inc["날짜"].str[:7]
    g = inc.pivot_table(index="월", columns="구분", values="금액", aggfunc=["sum", "count"], fill_value=0)
    res = pd.DataFrame({"월": g.index})
    for kind in ("입금", "출금"):
        res[f"{kind} 합계"] = g[("sum", kind)].values if ("sum", kind) in g.columns else 0
        res[f"{kind} 건수"] = g[("count", kind)].values if ("count", kind) in g.columns else 0
    return res.reset_index(drop=True)


def _safe(name):
    return re.sub(r"[^0-9A-Za-z가-힣()_\-]+", "_", name).strip("_") or "계좌"


def build_account_workbook(ledger, start, end, account, collected_from=None):
    """계좌 하나의 엑셀(.xlsx) 바이트. 시트: 요약 / 거래원장 / 월별요약 / 상쇄(제외) / 내부이체 / 미분류"""
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as xw:
        pd.DataFrame(account_summary(ledger, start, end, collected_from), columns=["항목", "내용"]).to_excel(xw, sheet_name="요약", index=False)
        ledger.to_excel(xw, sheet_name="거래원장", index=False)
        monthly_table(ledger).to_excel(xw, sheet_name="월별요약", index=False)
        ledger[ledger["상태"] == "상쇄(제외)"].to_excel(xw, sheet_name="상쇄(제외)", index=False)
        ledger[ledger["상태"] == "내부이체"].to_excel(xw, sheet_name="내부이체", index=False)
        ledger[ledger["상태"] == "미분류"].to_excel(xw, sheet_name="미분류", index=False)
        for ws in xw.book.worksheets:
            for col in ws.columns:
                width = max((len(str(c.value)) if c.value is not None else 0) for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, width * 1.6), 60)
            ws.freeze_panes = "A2"
            for c in ws[1]:
                c.font = c.font.copy(bold=True)
            for row in ws.iter_rows(min_row=2):
                for c in row:
                    if isinstance(c.value, float) or (isinstance(c.value, int) and c.column_letter != "A"):
                        c.number_format = "#,##0"
    return bio.getvalue()


def build_zip(workbooks, period_label, checklist_df):
    """workbooks: {계좌이름: xlsx bytes}. 폴더 구조: 00_자료체크리스트.csv, 01_은행내역/계좌_기간.xlsx"""
    bio = io.BytesIO()
    pl = _safe(period_label)
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("00_자료체크리스트.csv", checklist_df.to_csv(index=False).encode("utf-8-sig"))
        for acct, data in workbooks.items():
            z.writestr(f"01_은행내역/{_safe(acct)}_{pl}.xlsx", data)
    return bio.getvalue()
