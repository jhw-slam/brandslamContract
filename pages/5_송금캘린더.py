import os
import calendar
from datetime import date, datetime

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="전체 송금 스케쥴", layout="wide")

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

def projects_map():
    projs = SUPA.table("projects").select("id,brand,product,company_id,stage").execute().data
    comp = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    label = {p["id"]: f"{comp.get(p['company_id'],'')} · {p.get('product') or p['brand']}" for p in projs}
    return projs, comp, label

def load_events():
    return SUPA.table("cash_events").select("*").order("due_date").execute().data

projs, comp, plabel = projects_map()
events = load_events()
SLAB = {"LEAD":"제안·미팅","SENT":"계약서 발송","SIGNED":"서명 완료","DEPOSIT":"선금 입금","PROGRESS":"캠페인 진행","BALANCE":"잔금 청구","SETTLED":"정산 완료"}
pstage = {p["id"]: SLAB.get(p.get("stage"), p.get("stage") or "-") for p in projs}

st.title("전체 송금 스케쥴")

# ── 일정 추가 ─────────────────────────────────────────────────
with st.expander("➕ 송금 일정 추가", expanded=not events):
    with st.form("add_ev", clear_on_submit=True):
        c = st.columns([2, 1, 1, 1.2, 1.2])
        popts = {"(프로젝트 없음)": None}
        for p in projs:
            popts[plabel[p["id"]]] = p["id"]
        pj = c[0].selectbox("프로젝트", list(popts.keys()))
        direction = c[1].selectbox("구분", ["받을 돈", "나갈 돈"])
        category = c[2].selectbox("항목", ["선금", "잔금", "인보이스", "실행사 지급", "기타"])
        amount = c[3].number_input("금액", min_value=0, step=100000)
        due = c[4].date_input("예정일", value=date.today())
        c2 = st.columns([3, 1, 1])
        title = c2[0].text_input("메모/제목", placeholder="예: OWM 선금 청구")
        paid = c2[1].checkbox("완료(입금/지급)")
        if c2[2].form_submit_button("추가") and amount:
            SUPA.table("cash_events").insert({
                "project_id": popts[pj], "direction": "in" if direction == "받을 돈" else "out",
                "category": category, "title": title, "amount": int(amount),
                "due_date": due.isoformat(), "paid": paid,
                "paid_date": due.isoformat() if paid else None}).execute()
            st.rerun()

if not events:
    st.info("등록된 송금 일정이 없습니다. 위 '➕ 송금 일정 추가'로 입력하면 표·달력에 표시됩니다.")
    st.stop()

# ── 공통 요약 ─────────────────────────────────────────────────
df = pd.DataFrame(events)
df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce")
inc = df[df["direction"] == "in"]; out = df[df["direction"] == "out"]
k = st.columns(4)
k[0].metric("입금완료(받은)", won(inc[inc["paid"] == True]["amount"].sum()))
k[1].metric("미입금(받을)", won(inc[inc["paid"] != True]["amount"].sum()))
k[2].metric("지급완료(나간)", won(out[out["paid"] == True]["amount"].sum()))
k[3].metric("미지급(나갈)", won(out[out["paid"] != True]["amount"].sum()))

fc = st.columns([1.6, 1])
flt = fc[0].radio("상태 필터", ["전체", "미입금", "입금완료", "미지급"], horizontal=True)
proj_opts = ["전체 프로젝트"] + [plabel[p["id"]] for p in projs] + ["(프로젝트 없음)"]
projsel = fc[1].selectbox("프로젝트별 보기", proj_opts)
label2id = {plabel[p["id"]]: p["id"] for p in projs}

def _keep(e):
    if flt == "미입금": ok = e["direction"] == "in" and not e["paid"]
    elif flt == "입금완료": ok = e["direction"] == "in" and e["paid"]
    elif flt == "미지급": ok = e["direction"] == "out" and not e["paid"]
    else: ok = True
    if projsel == "(프로젝트 없음)": ok = ok and (e.get("project_id") is None)
    elif projsel != "전체 프로젝트": ok = ok and (e.get("project_id") == label2id.get(projsel))
    return ok

events_f = [e for e in events if _keep(e)]
st.caption(f"필터: {flt} · {projsel} · {len(events_f)}건")

mode = st.radio("보기", ["표로 보기", "달력으로 보기"], horizontal=True)

# ── 표로 보기 ─────────────────────────────────────────────────
if mode == "표로 보기":
    rows = []
    for e in events_f:
        rows.append({
            "예정일": (e.get("due_date") or "")[:10],
            "구분": "받을 돈" if e["direction"] == "in" else "나갈 돈",
            "프로젝트": plabel.get(e.get("project_id"), "-"),
            "단계": pstage.get(e.get("project_id"), "-"),
            "항목": e.get("category") or "",
            "제목": e.get("title") or "",
            "금액": won(e["amount"]),
            "상태": "완료" if e["paid"] else "예정",
        })
    tdf = pd.DataFrame(rows).sort_values("예정일")
    st.dataframe(tdf, use_container_width=True, hide_index=True)
    i_s = sum(e["amount"] or 0 for e in events_f if e["direction"] == "in")
    o_s = sum(e["amount"] or 0 for e in events_f if e["direction"] == "out")
    fin = st.columns(3)
    fin[0].metric("받을 합계", won(i_s))
    fin[1].metric("나갈 합계", won(o_s))
    fin[2].metric("순액 (받을 − 나갈)", won(i_s - o_s))

# ── 달력으로 보기 ─────────────────────────────────────────────
else:
    if "cal_y" not in st.session_state:
        t = date.today(); st.session_state.cal_y = t.year; st.session_state.cal_m = t.month
    nav = st.columns([1, 2, 1])
    if nav[0].button("◀ 이전 달"):
        m = st.session_state.cal_m - 1; y = st.session_state.cal_y
        if m < 1: m = 12; y -= 1
        st.session_state.cal_m, st.session_state.cal_y = m, y; st.rerun()
    nav[1].markdown(f"<h3 style='text-align:center'>{st.session_state.cal_y}년 {st.session_state.cal_m}월</h3>", unsafe_allow_html=True)
    if nav[2].button("다음 달 ▶"):
        m = st.session_state.cal_m + 1; y = st.session_state.cal_y
        if m > 12: m = 1; y += 1
        st.session_state.cal_m, st.session_state.cal_y = m, y; st.rerun()

    Y, M = st.session_state.cal_y, st.session_state.cal_m
    by_date = {}
    for e in events_f:
        dd = (e.get("due_date") or "")[:10]
        by_date.setdefault(dd, []).append(e)

    head = st.columns(7)
    for i, wd in enumerate(["일", "월", "화", "수", "목", "금", "토"]):
        head[i].markdown(f"<div style='text-align:center;color:#888;font-size:12px'>{wd}</div>", unsafe_allow_html=True)

    cal = calendar.Calendar(firstweekday=6)  # 일요일 시작
    for week in cal.monthdayscalendar(Y, M):
        cols = st.columns(7)
        for i, day in enumerate(week):
            if day == 0:
                cols[i].markdown("&nbsp;", unsafe_allow_html=True); continue
            iso = date(Y, M, day).isoformat()
            html = f"<div style='border:1px solid #eee;border-radius:6px;padding:4px;min-height:64px'>"
            html += f"<div style='font-weight:600;font-size:12px'>{day}</div>"
            for e in by_date.get(iso, []):
                col = "#1aa179" if e["direction"] == "in" else "#d4351c"
                sign = "+" if e["direction"] == "in" else "−"
                chk = "✓" if e["paid"] else ""
                html += f"<div style='font-size:10.5px;color:{col}'>{sign}{won_short(e['amount'])}{chk}</div>"
            html += "</div>"
            cols[i].markdown(html, unsafe_allow_html=True)
    me = [e for e in events_f if (e.get("due_date") or "")[:7] == f"{Y}-{M:02d}"]
    mi = sum(e["amount"] or 0 for e in me if e["direction"] == "in")
    mo = sum(e["amount"] or 0 for e in me if e["direction"] == "out")
    st.caption(f"이 달 합계 — 받을 {won(mi)} · 나갈 {won(mo)} · 순 {won(mi - mo)}")
    st.caption("초록 +금액 = 받을 돈 · 빨강 −금액 = 나갈 돈 · ✓ = 완료")
