import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 레포 루트의 bank_common.py / vat_package.py
from bank_common import OFFSET_WINDOW_MIN, mark_duplicates, parse_txn_datetime
from vat_package import (PERIODS, build_account_workbook, build_zip, default_period, find_date_gaps, period_deadline,
                         period_range, prepare_ledger)

st.set_page_config(page_title="VAT Filing", layout="wide")

# 재무캘린더와 같은 방식: FINANCE_PASSWORD 변수가 비어 있으면 로그인 없이 열린다(검토용, 10/9 대표 지시).
ADMIN_EMAIL = "jhw@slam-global.com"
FINANCE_PASSWORD = os.environ.get("FINANCE_PASSWORD")
if FINANCE_PASSWORD and not st.session_state.get("finance_ok"):
    st.warning("🔒 이 페이지는 장현우 전용입니다. 이메일과 비밀번호를 입력해주세요.")
    gc1, gc2 = st.columns(2)
    email_in = gc1.text_input("이메일", placeholder=ADMIN_EMAIL)
    pw_in = gc2.text_input("비밀번호", type="password")
    if st.button("입장"):
        if email_in.strip().lower() == ADMIN_EMAIL and pw_in == FINANCE_PASSWORD:
            st.session_state.finance_ok = True
            st.rerun()
        else:
            st.error("이메일 또는 비밀번호가 올바르지 않습니다.")
    st.stop()


@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()

TODAY = datetime.now(timezone(timedelta(hours=9))).date()


def tryq(fn, default):
    try:
        return fn(), None
    except Exception as e:
        return default, f"{type(e).__name__}: {str(e)[:140]}"


def fetch_all(table, cols, filt=None, order=None, page=1000):
    """PostgREST 는 한 번에 1000줄까지만 준다 → 나눠서 전부 읽는다."""
    out, start = [], 0
    while True:
        q = SUPA.table(table).select(cols)
        if filt:
            q = filt(q)
        if order:
            q = q.order(order).order("id") if order != "no_id" else q
        rows = q.range(start, start + page - 1).execute().data
        out += rows
        if len(rows) < page:
            return out
        start += page


@st.cache_data(ttl=60)
def load_period(start_iso, end_iso):
    """기간 안의 은행거래·세금계산서·캠페인과 보조 자료. 읽지 못한 건 에러 문구로 돌려줘서 화면이 이유를 보여준다."""
    errs = {}
    cats, errs["cats"] = tryq(lambda: SUPA.table("fin_account_categories").select("id,name,type").execute().data, [])
    bank, errs["bank"] = tryq(lambda: fetch_all(
        "bank_transactions", "id,direction,amount,txn_date,txn_datetime,description,account_label,account_category_id",
        lambda q: q.gte("txn_date", start_iso).lte("txn_date", end_iso), "txn_date"), [])
    keep_rows, errs["keep"] = tryq(lambda: SUPA.table("bank_offset_overrides").select("bank_transaction_id").eq("decision", "keep").execute().data, [])
    taxes, errs["tax"] = tryq(lambda: SUPA.table("tax_invoices").select("approval_no,write_date,buyer_name,total_amount,supply_amount,vat,kind,canceled")
                              .gte("write_date", start_iso).lte("write_date", end_iso).limit(2000).execute().data, [])
    camps, errs["camp"] = tryq(lambda: SUPA.table("sales_campaigns").select("id,campaign_name,open_date,invoice_url,invoice_amount,contract_url")
                               .gte("open_date", start_iso).lte("open_date", end_iso).limit(2000).execute().data, [])
    # 계좌별 수집 시작일(그 이전 기간은 비어 있을 수 있다는 안내용)
    labels = {r["account_label"] for r in bank if r.get("account_label")}
    recent, _ = tryq(lambda: SUPA.table("bank_transactions").select("account_label").order("txn_date", desc=True).limit(300).execute().data, [])
    labels |= {r["account_label"] for r in recent if r.get("account_label")}
    first_seen = {}
    for lb in labels:
        r, _ = tryq(lambda: SUPA.table("bank_transactions").select("txn_date").eq("account_label", lb).order("txn_date").limit(1).execute().data, [])
        if r:
            first_seen[lb] = r[0]["txn_date"]
    return {"cats": cats, "bank": bank, "keep": {k["bank_transaction_id"] for k in keep_rows}, "taxes": taxes, "camps": camps,
            "first_seen": first_seen, "errs": {k: v for k, v in errs.items() if v}}


def to_bank_df(rows, cats, keep_ids):
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    cat_by_id = {c["id"]: c for c in cats}
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    df["txn_date"] = pd.to_datetime(df["txn_date"], errors="coerce")
    df["txn_datetime"] = parse_txn_datetime(df["txn_datetime"])
    df["cat_name"] = df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("name", "미분류"))
    df["cat_type"] = df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("type"))
    return mark_duplicates(df, keep_ids)


def won(n):
    return f"₩{float(n or 0):,.0f}"


# ═════════════════════════════════════════════════════════════
st.title("🧾 VAT Filing — 부가세 신고 자료 준비")
st.caption("자료를 모으고 정리해서 세무사사무실이 바로 신고 업무를 시작하게 돕는 화면이에요. "
           "**세액 계산·신고서 작성·홈택스 제출은 세무사사무실이 합니다.** 기획안: docs/vat_filing_plan.md")

d_year, d_label = default_period(TODAY)
c1, c2, c3 = st.columns([1, 2, 3])
year = c1.selectbox("연도", [TODAY.year - 1, TODAY.year, TODAY.year + 1], index=[TODAY.year - 1, TODAY.year, TODAY.year + 1].index(d_year), key="vat_year")
labels = list(PERIODS.keys())
label = c2.selectbox("신고 구분", labels, index=labels.index(d_label), key="vat_label")
start, end = period_range(year, label)
deadline = period_deadline(year, label)
dday = (deadline - TODAY).days
c3.metric("신고 기한(법인 기준 추정)", f"{deadline:%Y-%m-%d}", delta=(f"D-{dday}" if dday >= 0 else f"{-dday}일 지남"), delta_color=("inverse" if 0 <= dday <= 14 else "off"))
st.caption(f"대상 기간 {start} ~ {end}. 예정신고 대상 여부와 정확한 기한은 세무사사무실에 확인하세요.")
if st.button("🔄 새로고침", key="vat_refresh"):
    load_period.clear(); st.rerun()

data = load_period(start.isoformat(), end.isoformat())
for k, v in data["errs"].items():
    st.warning(f"⚠️ '{k}' 자료를 읽지 못했어요 ({v}) — 해당 항목은 '확인 불가'로 표시돼요.")
bank_df = to_bank_df(data["bank"], data["cats"], data["keep"])
accounts = sorted(bank_df["account_label"].dropna().unique().tolist()) if not bank_df.empty else sorted(data["first_seen"].keys())

# ── 1) 자료 체크리스트 ────────────────────────────────────
st.subheader("1️⃣ 자료 체크리스트")
rows = []
gaps_by_acct = {}
for ac in accounts:
    sub = bank_df[bank_df["account_label"] == ac] if not bank_df.empty else pd.DataFrame()
    gaps = find_date_gaps([d.date() for d in sub["txn_date"].dropna()] if not sub.empty else [], start, end)
    gaps_by_acct[ac] = gaps
    first = data["first_seen"].get(ac)
    if sub.empty:
        status, detail = "❌ 기간 내 거래 없음", "자동 수집 대상인데 거래가 없어요 — 뱅크다 연동/수집 시작일 확인" + (f" (이 계좌 수집 시작: {first})" if first else "")
    elif gaps:
        status, detail = "⚠️ 확인 필요", f"{len(sub):,}건 · 거래가 7일 이상 없는 구간 {len(gaps)}곳 (아래 '검증'에서 확인)"
    else:
        status, detail = "✅ 자동 수집됨", f"{len(sub):,}건"
    rows.append({"자료": f"은행내역 — {ac}", "방식": "자동(뱅크다)", "상태": status, "상세": detail})

tax = data["taxes"]
active_tax = [t for t in tax if not t.get("canceled")]
rows.append({"자료": "매출 전자세금계산서", "방식": "홈택스 업로드(송금캘린더)",
             "상태": ("✅ 있음" if active_tax else "📥 올려주세요"),
             "상세": (f"{len(active_tax)}건 · 공급가 {won(sum(float(t.get('supply_amount') or 0) for t in active_tax))} · 세액 {won(sum(float(t.get('vat') or 0) for t in active_tax))} (취소 {len(tax) - len(active_tax)}건 제외)"
                      if active_tax else "이 기간 매출 세금계산서가 없어요. 홈택스 '매출 목록'을 받아 송금캘린더에 올려주세요") if "tax" not in data["errs"] else "읽지 못함"})
rows.append({"자료": "매입 전자세금계산서·계산서", "방식": "홈택스 파일 업로드", "상태": "📥 올려주세요", "상세": "업로드 기능은 2단계에서 열려요. 지금은 홈택스에서 받아 파일을 모아 두세요(아래 안내 카드)"})
rows.append({"자료": "법인카드 사용내역", "방식": "홈택스 또는 카드사 파일", "상태": "📥 올려주세요", "상세": "업로드 기능은 2단계에서 열려요. 홈택스에 사업용 카드를 등록하면 한 곳에서 받을 수 있어요(아래 안내 카드)"})
pp = bank_df[(bank_df["description"].fillna("").str.upper().str.contains("PAYPAL")) & (bank_df["direction"] == "out") & (~bank_df["is_dup_certain"])] if not bank_df.empty else pd.DataFrame()
rows.append({"자료": "해외·PayPal 지급", "방식": "은행(자동) + 페이팔 거래내역 파일",
             "상태": ("📌 은행에서 확인됨 · 페이팔 파일 필요" if len(pp) else "➖ 은행에 PayPal 출금 없음"),
             "상세": (f"은행 PAYPAL 출금 {len(pp)}건 · {won(pp['amount'].sum())} — 세무사사무실에 처리 방식(공제 여부 등) 확인 필요" if len(pp) else "PayPal 거래내역 파일이 있으면 함께 보관")})
camps = data["camps"]
no_inv = [c for c in camps if not c.get("invoice_url")]
rows.append({"자료": "캠페인 인보이스·계약서", "방식": "자동(캠페인 등록)", "상태": ("✅ 있음" if camps and not no_inv else ("⚠️ 인보이스 없는 캠페인 있음" if no_inv else "➖ 이 기간 캠페인 없음")),
             "상세": (f"캠페인 {len(camps)}건 중 인보이스 파일 없는 건 {len(no_inv)}건" if camps else "") if "camp" not in data["errs"] else "읽지 못함"})
rows.append({"자료": "급여·원천세·4대보험(대량송금)", "방식": "별도 DB(분할 후)", "상태": "➖ 아직 해당 없음", "상세": "DB 분할 후 대표님이 올릴 자료로 연결할 예정이에요"})
chk_df = pd.DataFrame(rows)
st.dataframe(chk_df, hide_index=True, use_container_width=True)
st.caption("📥 항목은 받아서 보관하셨는지 아래에 표시해 두세요. (지금은 이 화면을 새로고침하면 체크가 초기화돼요 — 저장 기능은 추후)")
mk1, mk2, mk3, mk4 = st.columns(4)
base_key = f"{year}_{label}"
mk1.checkbox("매입 세금계산서 받음", key=f"vat_got_buy_{base_key}")
mk2.checkbox("카드 사용내역 받음", key=f"vat_got_card_{base_key}")
mk3.checkbox("페이팔 거래내역 받음", key=f"vat_got_pp_{base_key}")
mk4.checkbox("기타 영수증 모음", key=f"vat_got_misc_{base_key}")

# ── 2) 단계별 안내 ────────────────────────────────────────
st.subheader("2️⃣ 단계별 안내 — 어디서 무엇을 받나")
st.caption("🔐 비밀번호는 여기에 저장하지 않아요. 로그인은 평소 쓰시는 방식(비밀번호 관리자·공동인증서)으로 하세요. 메뉴 이름은 사이트 개편으로 바뀔 수 있어요 — 안 보이면 세무사사무실에 물어보세요.")
with st.expander("🏛️ 홈택스 — 매입 전자세금계산서·계산서 (받은 것)", expanded=False):
    st.markdown("""
1. [홈택스](https://www.hometax.go.kr) 접속 → **공동인증서(또는 간편인증)** 로그인 (법인 대표/세무 담당 인증서)
2. **조회/발급 → 전자(세금)계산서 → 목록 조회**에서 **매입**을 선택하고 기간을 **{s} ~ {e}** 로 설정 (세금계산서와 계산서를 각각)
3. **엑셀(.xls/.csv) 다운로드** → 파일 이름에 기간을 붙여 보관 (예: `매입세금계산서_{y}.xls`)
4. 이 화면의 체크박스 '매입 세금계산서 받음'을 눌러 두세요. (업로드·자동 정리는 2단계에서 열려요)
""".format(s=start, e=end, y=f"{year}_{label.split(' ')[0]}{label.split(' ')[1][:2]}"))
with st.expander("🏛️ 홈택스 — 매출 전자세금계산서 (발행한 것)"):
    st.markdown("""
1. 홈택스 로그인 → **조회/발급 → 전자(세금)계산서 → 목록 조회**에서 **매출** 선택, 기간 {s} ~ {e}
2. 엑셀 다운로드 → **재무 › 송금캘린더**의 '홈택스 세금계산서 목록' 업로드에 그대로 올리세요(이미 있는 기능).
""".format(s=start, e=end))
with st.expander("💳 법인카드 사용내역 — 홈택스 한 곳에서 받기 (추천) / 카드사에서 받기"):
    st.markdown("""
**지금은 홈택스에 법인카드가 등록돼 있지 않거나 확실하지 않아요.** 등록하면 카드사별 로그인 없이 한 곳에서 받을 수 있어요.
1. 홈택스 로그인 → 전자세금계산서·현금영수증·신용카드 쪽의 **사업용 신용카드 등록** 메뉴에서 법인카드를 등록 (카드번호와 카드사 확인 필요 — 메뉴 이름은 세무사사무실에 확인)
2. 등록 후 **사업용 신용카드 매입내역 조회**에서 기간 {s} ~ {e} 를 받아 엑셀로 저장 (등록 직후에는 과거 내역이 바로 안 보일 수 있어요 — 세무사사무실에 문의)
3. **등록이 어렵다면**: 각 카드사 사이트 → 이용내역 조회 → 기간 설정 → 엑셀 다운로드 (카드마다 반복)
4. 받은 파일은 '카드 사용내역 받음'에 체크. 카드사 로그인 정보는 **비밀번호 관리자**에 두세요(여기에 저장 안 함).
""".format(s=start, e=end))
with st.expander("🅿️ PayPal — 거래내역 (해외 인플루언서 지급)"):
    st.markdown("""
1. [PayPal](https://www.paypal.com) 로그인 → **활동(Activity) → 보고서/내역 다운로드**에서 기간 {s} ~ {e}, 형식 **CSV** 선택 (메뉴 이름은 PayPal 개편에 따라 달라요)
2. **통화별 거래**가 섞이지 않도록 전체 거래(모든 거래)로 받기
3. 은행에 보이는 `PAYPAL *이름` 출금과 대조할 수 있게 원본 그대로 보관
4. 해외 인플루언서 지급을 세무상 어떻게 처리할지(공제·증빙)는 **세무사사무실 확인 사항**이에요.
""".format(s=start, e=end))
with st.expander("🏦 은행내역 (SC제일·기업) — 자동 수집됨"):
    st.markdown("뱅크다로 자동 수집돼요. 아래 3️⃣에서 계좌별·기간별 엑셀로 내려받을 수 있고, 거래가 없는 구간이나 미분류가 있으면 알려드려요.")

# ── 3) 은행내역 정리 ──────────────────────────────────────
st.subheader("3️⃣ 은행내역 정리 — 계좌별·기간별 엑셀")
if bank_df.empty:
    st.info("이 기간의 은행거래가 없어요. (뱅크다 수집 시작일 이전이거나 아직 수집되지 않았을 수 있어요)")
else:
    mask = st.checkbox("급여·인건비 거래의 적요(개인 이름)는 가리기 (권장)", value=True, key="vat_mask",
                       help="부가세 신고에는 개인별 급여 내역이 필요 없어요. 세무사사무실이 급여 자료를 따로 받는다면 그쪽으로 전달하세요.")
    st.caption(f"상쇄 규칙: 같은 계좌·같은 날·같은 금액의 입금과 출금이 {OFFSET_WINDOW_MIN}분 안에 있으면 결제↔취소 쌍으로 보고 집계에서 빼요(데이터는 지우지 않고 별도 시트에 남겨요).")
    workbooks = {}
    for ac in accounts:
        sub = bank_df[bank_df["account_label"] == ac]
        if sub.empty:
            continue
        ledger = prepare_ledger(sub, mask_labor=mask)
        first = data["first_seen"].get(ac)
        collected_from = pd.to_datetime(first).date() if first else None
        workbooks[ac] = build_account_workbook(ledger, start, end, ac, collected_from)
        inc = ledger[ledger["상태"] != "상쇄(제외)"]
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(f"{ac} 거래", f"{len(ledger):,}건")
        m2.metric("입금(상쇄 제외)", won(inc[inc["구분"] == "입금"]["금액"].sum()))
        m3.metric("출금(상쇄 제외)", won(inc[inc["구분"] == "출금"]["금액"].sum()))
        m4.metric("상쇄 제외", f"{int((ledger['상태'] == '상쇄(제외)').sum())}건")
        m5.metric("미분류", f"{int((ledger['상태'] == '미분류').sum())}건")
        st.download_button(f"⬇️ {ac} 엑셀 내려받기", workbooks[ac], file_name=f"{ac}_{year}_{label.split(' ')[0]}{label.split(' ')[1][:2]}.xlsx".replace("/", "_"),
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key=f"vat_xlsx_{ac}")
    if workbooks:
        st.download_button("📦 전체 패키지(ZIP) 내려받기 — 체크리스트 + 계좌별 엑셀", build_zip(workbooks, f"{year}_{label}", chk_df),
                           file_name=f"부가세자료_{year}_{label.split(' ')[0]}{label.split(' ')[1][:2]}.zip", mime="application/zip", key="vat_zip")
        st.caption("드라이브 자동 업로드는 3단계(공유 드라이브 준비 후)에 열려요. 그 전에는 이 ZIP을 풀어 세무사 공유 폴더에 올려주세요.")

    # ── 4) 검증 ──
    st.subheader("4️⃣ 검증 — 빠진 것·의심 구간")
    problems = []
    for ac, gaps in gaps_by_acct.items():
        for g0, g1, n, why in gaps:
            problems.append({"계좌": ac, "구간": f"{g0} ~ {g1}", "일수": n, "내용": f"{why} (7일 이상 거래 없음 — 실제로 거래가 없었는지 확인)"})
    n_unc = int(((bank_df["account_category_id"].isna()) & (~bank_df["is_dup_certain"])).sum())
    n_sus = int(bank_df["is_dup_suspect"].sum())
    n_off = int(bank_df["is_dup_certain"].sum())
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("거래 없는 의심 구간", f"{len(problems)}곳")
    v2.metric("미분류 거래", f"{n_unc}건", help="재무캘린더에서 분류하면 줄어들어요")
    v3.metric("애매한 상쇄 의심", f"{n_sus}건")
    v4.metric("상쇄 제외", f"{n_off}건")
    if problems:
        st.dataframe(pd.DataFrame(problems), hide_index=True, use_container_width=True)
    else:
        st.success("거래가 7일 이상 비어 있는 구간은 없어요.")
    if n_unc or n_sus:
        st.info("미분류·애매한 의심은 **재무캘린더 › 전체 매칭 현황 › 은행거래내역**에서 정리할 수 있어요. 안 해도 엑셀에는 '미분류' 시트로 남아요.")
        try:
            st.page_link("pages/9_재무캘린더.py", label="💹 재무캘린더로 이동")
        except Exception:
            pass
st.caption("다음 단계: ② 매입 세금계산서·카드 파일 업로드·정리 → ③ 드라이브 자동 업로드·세무사 알림 → ④ DB 분할 후 급여·원천세 연결")
