import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 레포 루트의 bank_common.py / vat_package.py
from bank_common import OFFSET_WINDOW_MIN, apply_internal_rule, mark_duplicates, parse_txn_datetime
from vat_package import (PERIODS, build_account_workbook, build_zip, default_period, find_date_gaps, period_deadline,
                         period_range, prepare_ledger)
import drive_upload as du
from hometax_files import check_list, check_pdf, parse_hometax_list, parse_invoice_pdf
from vat_requests import AUTO, COMPANY_BIZ_NO, REQUEST_INFO, STEP_BY_ID, STEPS, step_status

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
    df = apply_internal_rule(df, cats)   # 회사 이름이 적힌 거래 = 내부이체
    return mark_duplicates(df, keep_ids)


def won(n):
    return f"₩{float(n or 0):,.0f}"


# ── 구글드라이브 연결 ──────────────────────────────────────
OUR_BIZ_NO = os.environ.get("COMPANY_BIZ_NO") or COMPANY_BIZ_NO


@st.cache_resource
def get_svc():
    return du.build_service()


@st.cache_data(ttl=20, show_spinner=False)
def drive_listing(folder_id, ver):
    """세무사 공유 폴더의 파일 현황(20초 캐시, 올린 뒤에는 clear). 반환 {"err":..., "folder":..., "root":[...], "sub":{폴더이름:[파일...]}}"""
    svc, err = get_svc()
    if err:
        return {"err": err}
    try:
        folder = du.get_folder(svc, folder_id)
        root = du.list_children(svc, folder_id)
        sub = {}
        wanted = {st_["folder"] for st_ in STEPS}
        for c in root:
            if c["mimeType"] == du.FOLDER_MIME and c["name"] in wanted:
                sub[c["name"]] = du.list_children(svc, c["id"])
        return {"err": None, "folder": folder, "root": root, "sub": sub}
    except Exception as e:
        return {"err": du.explain_error(e)}


def bump_and_rerun(msg=None, kind="ok"):
    if msg:
        st.session_state["vat_flash"] = (kind, msg)
    st.session_state["vat_ver"] = st.session_state.get("vat_ver", 0) + 1
    drive_listing.clear()
    st.rerun()


# ═════════════════════════════════════════════════════════════
st.title("🧾 VAT Filing — 세무사 요청 자료 제출")
st.caption("세무사사무실이 요청한 자료를 **한 단계씩 따라 하면 끝나게** 만든 화면이에요. 파일을 올리면 세무사 공유 폴더(구글드라이브)에 바로 정리돼요. "
           "**세액 계산·신고서 작성·홈택스 제출은 세무사사무실이 합니다.** (기획안: docs/vat_filing_plan.md)")
flash = st.session_state.pop("vat_flash", None)
if flash:
    {"ok": st.success, "warn": st.warning, "bad": st.error}.get(flash[0], st.info)(flash[1])

d_year, d_label = default_period(TODAY)
c1, c2, c3 = st.columns([1, 2, 3])
years = [TODAY.year - 1, TODAY.year, TODAY.year + 1]
year = c1.selectbox("연도", years, index=years.index(d_year), key="vat_year")
labels = list(PERIODS.keys())
label = c2.selectbox("신고 구분", labels, index=labels.index(d_label), key="vat_label")
start, end = period_range(year, label)
info = REQUEST_INFO.get((year, label))
if info:
    dd = (info["deadline"] - TODAY).days
    c3.metric("세무사 자료 요청 기한", f"{info['deadline']:%Y-%m-%d}", delta=(f"D-{dd}" if dd >= 0 else f"{-dd}일 지남"), delta_color=("inverse" if dd <= 3 else "off"))
    st.info(f"📨 {info['from']} — {info['notice']}")
else:
    deadline = period_deadline(year, label)
    dd = (deadline - TODAY).days
    c3.metric("신고 기한(법인 기준 추정)", f"{deadline:%Y-%m-%d}", delta=(f"D-{dd}" if dd >= 0 else f"{-dd}일 지남"), delta_color="off")
    st.caption("이 기간은 세무사 요청 메일 정보가 아직 없어서 기본 단계로 보여줘요. 정확한 기한은 세무사사무실에 확인하세요.")
st.caption(f"대상 기간 {start} ~ {end}")

data = load_period(start.isoformat(), end.isoformat())
for k, v in data["errs"].items():
    st.warning(f"⚠️ '{k}' 자료를 읽지 못했어요 ({v}) — 해당 자동 정리는 건너뛰어요.")
bank_df = to_bank_df(data["bank"], data["cats"], data["keep"])
accounts = sorted(bank_df["account_label"].dropna().unique().tolist()) if not bank_df.empty else []
l0, l1 = label.split(" ")[0], label.split(" ")[1]
ptag = f"{year}년 {l0}{l1}"

# ── 0) 드라이브 연결 ──────────────────────────────────────
st.subheader("📁 세무사 공유 폴더 연결")
env_folder, env_name = du.configured_folder_id()
manual = st.text_input("폴더 주소 또는 ID (환경변수가 없거나 다른 폴더를 쓰고 싶을 때만)", key="vat_folder_in", placeholder="https://drive.google.com/drive/folders/…",
                       help="세무사사무실 공유 폴더 중 이번 분기 폴더(예: '브랜드슬램26년2분기')의 주소를 붙여넣으세요. 비워두면 환경변수를 써요.")
folder_id = du.folder_id_from_text(manual) if manual.strip() else env_folder
if manual.strip() and not folder_id:
    st.error("폴더 주소를 읽지 못했어요. '…/folders/' 뒤의 긴 글자(ID)가 보이게 붙여넣어 주세요.")
ver = st.session_state.get("vat_ver", 0)
listing = {"err": "폴더가 아직 정해지지 않았어요 (Railway 변수 TAX_DRIVE_FOLDER_ID 또는 DRIVE_FOLDER_ID, 또는 위 칸에 폴더 주소)"}
if folder_id:
    listing = drive_listing(folder_id, ver)
drive_ok = not listing.get("err")
if drive_ok:
    fo = listing["folder"]
    kind = "공유 드라이브" if fo.get("driveId") else "내 드라이브(개인)"
    src = "붙여넣은 주소" if manual.strip() else f"환경변수 {env_name}"
    st.success(f"✅ 폴더에 연결됨 — '{fo['name']}' ({kind}, {src}) · 폴더 안 항목 {len(listing['root'])}개")
    if fo["name"] and not any(k in fo["name"] for k in ("분기", "세무", "부가")):
        st.caption("⚠️ 폴더 이름이 이번 분기 폴더처럼 보이지 않아요. 맞는 폴더인지 확인해주세요.")
    if fo.get("webViewLink"):
        st.link_button("📂 드라이브에서 폴더 열기", fo["webViewLink"])
else:
    st.warning(f"드라이브에 아직 연결되지 않았어요 — {listing['err']}")
with st.expander("🔧 연결 점검 (올릴 수 있는지 확인)"):
    st.caption("폴더를 읽을 수 있는지, 작은 파일을 올렸다 지울 수 있는지 확인해요. 올릴 수 없으면 이유와 해결 방법을 알려드려요.")
    if st.button("연결 점검하기", key="vat_diag", disabled=not folder_id):
        svc, err = get_svc()
        if err:
            st.error(err)
        else:
            sa = getattr(svc, "_sa_email", "")
            if sa:
                st.caption(f"서비스계정: {sa} — 폴더에서 이 계정을 **편집자**로 공유해야 올릴 수 있어요.")
            for name, ok, msg in du.diagnose(svc, folder_id):
                (st.success if ok else st.error)(f"{'✅' if ok else '❌'} {name}: {msg}")

# ── 1) 진행 현황 ──────────────────────────────────────────
st.subheader("✅ 진행 현황")
statuses = {}
for stp in STEPS:
    names = [f["name"] for f in listing.get("sub", {}).get(stp["folder"], [])] if drive_ok else []
    statuses[stp["id"]] = step_status(stp["id"], names)
done_n = sum(1 for v in statuses.values() if v[0] == "✅")
st.progress(done_n / len(STEPS), text=f"{len(STEPS)}단계 중 {done_n}단계 완료" + ("" if drive_ok else " (드라이브 연결 후 표시돼요)"))
cols = st.columns(len(STEPS))
for col, stp in zip(cols, STEPS):
    ic, tx = statuses[stp["id"]]
    col.markdown(f"**{stp['icon']} {stp['no']}. {stp['title'].split(' + ')[0].split(' (')[0]}**\n\n{ic} {tx}")
if drive_ok and listing["root"]:
    root_files = [f for f in listing["root"] if f["mimeType"] != du.FOLDER_MIME]
    if root_files:
        st.caption("📎 폴더 바로 아래에 이미 있는 파일: " + " · ".join(f"[{f['name']}]({f['webViewLink']})" if f.get("webViewLink") else f["name"] for f in root_files))

# ── 2) 단계별 제출 ────────────────────────────────────────
st.subheader("📝 단계별 제출 — 위에서부터 하나씩")
st.caption("🔐 비밀번호는 이 화면에서 받거나 저장하지 않아요. 로그인은 평소 쓰시는 방식(비밀번호 관리자·공동인증서)으로 하세요. 메뉴 이름은 사이트 개편으로 바뀔 수 있어요.")


def drive_err(e):
    """올리기 실패 안내: 이유 + 어느 서비스계정을 어느 폴더에 편집자로 공유해야 하는지까지 보여준다."""
    st.error(f"❌ 올리지 못했어요 — {du.explain_error(e)}")
    sa = getattr(get_svc()[0], "_sa_email", "") if get_svc()[0] else ""
    fn = listing["folder"]["name"] if drive_ok else "(폴더 확인 전)"
    st.caption(f"📌 접속한 서비스계정: {sa or '-'} · 올리려던 폴더: {fn} — 이 계정이 **이 폴더**의 '편집자'로 공유돼 있어야 해요.")


def folder_for(step):
    """단계 폴더 ID(없으면 만든다). 반환 (id, 에러문구)"""
    svc, err = get_svc()
    if err:
        return None, err
    try:
        fid, _ = du.ensure_subfolder(svc, folder_id, step["folder"])
        return fid, None
    except Exception as e:
        return None, du.explain_error(e)


def existing_names(fid):
    svc, _ = get_svc()
    return {c["name"] for c in du.list_children(svc, fid)}


def build_bank_workbooks(mask=True):
    wbs = {}
    for ac in accounts:
        sub = bank_df[bank_df["account_label"] == ac]
        if sub.empty:
            continue
        ledger = prepare_ledger(sub, mask_labor=mask)
        first = data["first_seen"].get(ac)
        wbs[ac] = build_account_workbook(ledger, start, end, ac, pd.to_datetime(first).date() if first else None)
    return wbs


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def render_files(step):
    files = listing.get("sub", {}).get(step["folder"], []) if drive_ok else []
    if files:
        st.markdown("**지금 폴더에 있는 파일**")
        for f in sorted(files, key=lambda x: x["name"]):
            st.markdown(f"- [{f['name']}]({f['webViewLink']})" if f.get("webViewLink") else f"- {f['name']}")
    elif drive_ok:
        st.caption("아직 이 단계 폴더에 파일이 없어요.")


def upload_form(step, extra_ui=None):
    """파일 올리기 폼. 반환 없음 — 눌렀을 때만 처리한다(고르는 동안은 아무것도 실행하지 않는다)."""
    with st.form(f"vat_form_{step['id']}_{ver}"):
        ups = st.file_uploader(step["upload_label"], accept_multiple_files=True, type=step["accept"], key=f"vat_up_{step['id']}_{ver}")
        extra = extra_ui() if extra_ui else {}
        allow = st.checkbox("경고가 있어도 올리기", key=f"vat_allow_{step['id']}_{ver}") if step["id"] == "tax" else False
        go = st.form_submit_button(f"📤 이 단계 파일 올리기 (여기서 올라가요)", type="primary", disabled=not drive_ok)
    if not go:
        return
    if not ups and not (extra and extra.get("memo")):
        st.info("올릴 파일을 먼저 골라주세요.")
        return
    fid, err = folder_for(step)
    if err:
        st.error(f"❌ {err}")
        return
    msgs, plan = [], []
    for f in ups or []:
        data_b = f.getvalue()
        low = f.name.lower()
        if step["id"] == "tax" and low.endswith((".xls", ".xlsx", ".csv")):
            parsed = parse_hometax_list(data_b, f.name)
            for lv, m in check_list(parsed, None, start, end):
                msgs.append((lv, f"{f.name}: {m}"))
            if not parsed.get("error"):
                per = f"{parsed['period'][0]} ~ {parsed['period'][1]}" if parsed["period"] else "기간 불명"
                msgs.append(("info", f"{f.name}: {parsed['kind'] or '?'} 목록 · {per} · {len(parsed['rows'])}건 · 공급가 {won(parsed['supply'])} · 세액 {won(parsed['vat'])}"))
        elif step["id"] == "tax" and low.endswith(".pdf"):
            parsed = parse_invoice_pdf(data_b)
            for lv, m in check_pdf(parsed, start, end, OUR_BIZ_NO):
                msgs.append((lv, f"{f.name}: {m}"))
            if not parsed.get("error"):
                msgs.append(("info", f"{f.name}: 전자세금계산서 {len(parsed['invoices'])}건 · 공급가 {won(sum(i['supply'] for i in parsed['invoices']))}"))
        plan.append((f.name, data_b, f.type or "application/octet-stream"))
    for lv, m in msgs:
        {"ok": st.success, "info": st.info, "warn": st.warning, "bad": st.error}.get(lv, st.info)(("✅ " if lv == "ok" else "⚠️ " if lv == "warn" else "❌ " if lv == "bad" else "ℹ️ ") + m)
    if any(lv == "bad" for lv, _ in msgs) and not allow:
        st.error("🚫 문제가 있는 파일이 있어서 올리지 않았어요. 파일을 확인하거나 '경고가 있어도 올리기'를 체크하고 다시 올려주세요.")
        return
    try:
        names = existing_names(fid)
        n_ok = 0
        for name, b, mime in plan:
            uname = du.unique_name(names, name)
            du.upload_bytes(get_svc()[0], fid, uname, b, mime); names.add(uname); n_ok += 1
        if extra and extra.get("memo"):
            du.replace_or_upload(get_svc()[0], fid, AUTO + extra["memo"][0], extra["memo"][1].encode("utf-8"), "text/plain")
        bump_and_rerun(f"✅ {step['title'].split(' (')[0]}: 파일 {n_ok}개" + (" + 메모" if extra and extra.get("memo") else "") + "를 올렸어요.")
    except Exception as e:
        drive_err(e)


for stp in STEPS:
    ic, tx = statuses[stp["id"]]
    with st.expander(f"{ic} {stp['icon']} {stp['no']}단계 — {stp['title']}   ·   {tx}", expanded=(ic != "✅" and stp["no"] == next((s_["no"] for s_ in STEPS if statuses[s_["id"]][0] != "✅"), 0))):
        st.markdown(f"> 세무사 요청: *{stp['quote']}*")
        for g in stp["guide"]:
            st.markdown("- " + g.replace("{s}", str(start)).replace("{e}", str(end)))
        render_files(stp)

        if stp["id"] == "bank":
            if not accounts:
                st.info("이 기간의 은행거래가 없어서 자동으로 만들 엑셀이 없어요.")
            else:
                mask = st.checkbox("급여·인건비 거래의 적요(개인 이름)는 가리기 (권장)", value=True, key="vat_mask")
                wbs = build_bank_workbooks(mask)
                st.caption("자동 생성: " + " · ".join(f"{a} ({len(bank_df[bank_df['account_label'] == a]):,}건)" for a in wbs) + " — 상쇄(결제↔취소)·내부이체·미분류는 별도 시트로 나눠요.")
                with st.form(f"vat_bank_auto_{ver}"):
                    go_b = st.form_submit_button("📤 계좌별 은행내역 엑셀을 '은행' 폴더에 올리기 (같은 이름은 교체돼요)", type="primary", disabled=not drive_ok)
                if go_b:
                    fid, err = folder_for(stp)
                    if err:
                        st.error(f"❌ {err}")
                    else:
                        try:
                            for ac, b in wbs.items():
                                du.replace_or_upload(get_svc()[0], fid, f"{AUTO}{ac} {ptag} 거래내역.xlsx", b, XLSX)
                            bump_and_rerun(f"✅ 은행내역 엑셀 {len(wbs)}개를 올렸어요.")
                        except Exception as e:
                            drive_err(e)
                for ac, b in wbs.items():
                    st.download_button(f"⬇️ {ac} 엑셀 내려받기", b, file_name=f"{ac}_{ptag}.xlsx", mime=XLSX, key=f"vat_dl_{ac}")
            upload_form(stp)

        elif stp["id"] == "export":
            camps = [c for c in data["camps"] if c.get("invoice_url")]
            if camps:
                st.markdown("**캠페인에 등록된 인보이스** (이 기간 오픈 캠페인)")
                inv_df = pd.DataFrame([{"캠페인": c.get("campaign_name"), "오픈일": c.get("open_date"), "인보이스 금액": c.get("invoice_amount"), "인보이스 링크": c.get("invoice_url")} for c in camps])
                st.dataframe(inv_df, hide_index=True, use_container_width=True)
                with st.form(f"vat_inv_auto_{ver}"):
                    go_i = st.form_submit_button("📤 인보이스 목록 엑셀을 폴더에 올리기", disabled=not drive_ok)
                if go_i:
                    fid, err = folder_for(stp)
                    if err:
                        st.error(f"❌ {err}")
                    else:
                        import io as _io
                        bio = _io.BytesIO()
                        inv_df.to_excel(bio, sheet_name="인보이스", index=False)
                        try:
                            du.replace_or_upload(get_svc()[0], fid, f"{AUTO}캠페인 인보이스 목록 {ptag}.xlsx", bio.getvalue(), XLSX)
                            bump_and_rerun("✅ 인보이스 목록을 올렸어요.")
                        except Exception as e:
                            drive_err(e)
            else:
                st.caption("이 기간에 인보이스가 등록된 캠페인이 없어요.")
            upload_form(stp)

        elif stp["id"] == "influencer":
            pp = bank_df[(bank_df["description"].fillna("").str.upper().str.contains("PAYPAL")) & (bank_df["direction"] == "out") & (~bank_df["is_dup_certain"])] if not bank_df.empty else pd.DataFrame()
            if len(pp):
                st.markdown(f"**은행에서 찾은 PAYPAL 출금 {len(pp)}건 · {won(pp['amount'].sum())}**")
                pp_df = pd.DataFrame({"날짜": pp["txn_date"].dt.strftime("%Y-%m-%d"), "계좌": pp["account_label"], "적요": pp["description"], "금액": pp["amount"].astype(float)}).sort_values("날짜")
                st.dataframe(pp_df.assign(금액=pp_df["금액"].map(won)), hide_index=True, use_container_width=True, height=220)
                with st.form(f"vat_pp_auto_{ver}"):
                    go_p = st.form_submit_button("📤 PAYPAL 출금 목록 엑셀을 폴더에 올리기", disabled=not drive_ok)
                if go_p:
                    fid, err = folder_for(stp)
                    if err:
                        st.error(f"❌ {err}")
                    else:
                        import io as _io
                        bio = _io.BytesIO()
                        pp_df.to_excel(bio, sheet_name="PAYPAL 출금", index=False)
                        try:
                            du.replace_or_upload(get_svc()[0], fid, f"{AUTO}은행 PAYPAL 출금 목록 {ptag}.xlsx", bio.getvalue(), XLSX)
                            bump_and_rerun("✅ PAYPAL 출금 목록을 올렸어요.")
                        except Exception as e:
                            drive_err(e)
            else:
                st.caption("이 기간 은행에서 PAYPAL 출금이 보이지 않아요.")
            upload_form(stp)

        elif stp["id"] == "card":
            def _card_extra():
                fee = st.radio("연회비가 있나요?", ["없음", "있음", "카드마다 달라요 / 잘 모르겠어요"], horizontal=True, key=f"vat_fee_{ver}", index=None)
                note = st.text_input("메모 (카드별 연회비 등, 선택)", key=f"vat_feenote_{ver}")
                if fee is None:
                    return {}
                return {"memo": ("연회비_확인.txt", f"기간: {ptag}\n연회비: {fee}\n메모: {note or '-'}\n작성일: {TODAY}\n")}
            upload_form(stp, _card_extra)

        else:
            upload_form(stp)

# ── 3) 점검 · 대체 경로 ───────────────────────────────────
with st.expander("🔎 데이터 점검과 내려받기 (드라이브를 안 쓸 때 대체 경로)"):
    if bank_df.empty:
        st.info("이 기간의 은행거래가 없어요. (뱅크다 수집 시작일 이전이거나 아직 수집되지 않았을 수 있어요)")
    else:
        problems = []
        for ac in accounts:
            sub = bank_df[bank_df["account_label"] == ac]
            for g0, g1, n, why in find_date_gaps([d.date() for d in sub["txn_date"].dropna()], start, end):
                problems.append({"계좌": ac, "구간": f"{g0} ~ {g1}", "일수": n, "내용": f"{why} (7일 이상 거래 없음 — 실제로 없었는지 확인)"})
        n_unc = int(((bank_df["account_category_id"].isna()) & (~bank_df["is_dup_certain"])).sum())
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("거래 없는 의심 구간", f"{len(problems)}곳")
        v2.metric("미분류 거래", f"{n_unc}건")
        v3.metric("애매한 상쇄 의심", f"{int(bank_df['is_dup_suspect'].sum())}건")
        v4.metric("상쇄 제외", f"{int(bank_df['is_dup_certain'].sum())}건")
        if problems:
            st.dataframe(pd.DataFrame(problems), hide_index=True, use_container_width=True)
        st.caption(f"상쇄 규칙: 같은 계좌·같은 날·같은 금액의 입금과 출금이 {OFFSET_WINDOW_MIN}분 안에 있으면 결제↔취소 쌍으로 보고 집계에서 빼요(데이터는 지우지 않아요).")
        chk = pd.DataFrame([{"단계": f"{s_['no']}. {s_['title']}", "상태": f"{statuses[s_['id']][0]} {statuses[s_['id']][1]}"} for s_ in STEPS])
        wb_all = build_bank_workbooks(True)
        if wb_all:
            st.download_button("📦 전체 패키지(ZIP) 내려받기 — 진행 현황 + 계좌별 엑셀", build_zip(wb_all, f"{year}_{label}", chk),
                               file_name=f"부가세자료_{ptag}.zip", mime="application/zip", key="vat_zip")
        try:
            st.page_link("pages/9_재무캘린더.py", label="💹 재무캘린더에서 미분류·상쇄 정리하기")
        except Exception:
            pass
