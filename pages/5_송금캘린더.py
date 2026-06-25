import os
import io
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
CATS = ["선금", "잔금", "인보이스", "실행사 지급", "기타"]

def projects_map():
    projs = SUPA.table("projects").select("id,brand,product,company_id,stage").execute().data
    comp = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    label = {p["id"]: f"{comp.get(p['company_id'],'')} · {p.get('product') or p['brand']}" for p in projs}
    return projs, label

def load_events():
    return SUPA.table("cash_events").select("*").order("due_date").execute().data

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
view = top[3].radio("보기", ["달력", "리스트"], horizontal=True)

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

# ── 동작 버튼(팝오버): 일정 추가 · 지출 대량 업로드 ───────────
act = st.columns([1.1, 1.5, 5])
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
    b = st.columns([1, 1, 3])
    if b[0].button("저장", type="primary"):
        SUPA.table("cash_events").update({
            "direction": "in" if dirv.startswith("받을") else "out", "category": catv,
            "amount": int(amtv), "due_date": duev.isoformat(), "title": titv, "paid": paidv,
            "paid_date": duev.isoformat() if paidv else None}).eq("id", e["id"]).execute()
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

else:  # 리스트
    st.caption(f"{period} · {projsel} · {flt} · {len(events)}건 (오른쪽 '수정'으로 편집·완료·삭제)")
    h = st.columns([1.1, 0.8, 2.2, 1.3, 1.2, 0.8, 0.7])
    for i, t in enumerate(["예정일", "구분", "프로젝트", "항목/제목", "금액", "상태", ""]):
        h[i].markdown(f"<div style='color:#888;font-size:12px'>{t}</div>", unsafe_allow_html=True)
    for e in sorted(events, key=lambda x: (x.get("due_date") or "")):
        c = st.columns([1.1, 0.8, 2.2, 1.3, 1.2, 0.8, 0.7])
        c[0].write((e.get("due_date") or "-")[:10])
        c[1].write("받을" if e["direction"] == "in" else "나갈")
        c[2].write(plabel.get(e.get("project_id"), "-"))
        c[3].write(f"{e.get('category') or ''} {('· ' + e['title']) if e.get('title') else ''}")
        c[4].write(won(e["amount"]))
        c[5].write("✅" if e["paid"] else "⏳")
        if c[6].button("수정", key="ed" + e["id"]):
            st.session_state.edit_target = e; st.rerun()

# ── 하단 요약 (수익률) ────────────────────────────────────────
st.divider()
i_s = sum(e["amount"] or 0 for e in events if e["direction"] == "in")
o_s = sum(e["amount"] or 0 for e in events if e["direction"] == "out")
rate = round((i_s - o_s) / i_s * 100, 1) if i_s else 0
s = st.columns(4)
s[0].metric("받을 합계", won(i_s))
s[1].metric("나갈 합계", won(o_s))
s[2].metric("순액 (받을 − 나갈)", won(i_s - o_s))
s[3].metric("수익률", f"{rate}%")
