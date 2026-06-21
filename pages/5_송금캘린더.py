import os
import calendar
from datetime import date, datetime

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

def projects_map():
    projs = SUPA.table("projects").select("id,brand,product,company_id").execute().data
    comp = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    label = {p["id"]: f"{comp.get(p['company_id'],'')} · {p.get('product') or p['brand']}" for p in projs}
    return projs, comp, label

def load_events():
    return SUPA.table("cash_events").select(
        "id, project_id, direction, category, title, amount, due_date, paid, paid_date"
    ).order("due_date").execute().data

projs, comp, plabel = projects_map()
events = load_events()

st.title("📅 송금 캘린더")
st.caption("💡 **팁**: 각 송금 건별로 프로젝트를 할당하고 관리하세요. 같은 프로젝트의 여러 송금 건을 한 번에 볼 수 있습니다.")

# ── 프로젝트 필터 (ID 기반) ───────────────────────────────────
proj_filter_opts = {"(전체)": None}
for p in projs:
    proj_filter_opts[plabel[p["id"]]] = p["id"]

col1, col2 = st.columns([3, 2])
proj_filter = col1.selectbox(
    "🔗 프로젝트별 보기", 
    list(proj_filter_opts.keys()), 
    index=0,
    help="특정 프로젝트의 송금 일정을 따로 볼 수 있습니다. (프로젝트 ID로 필터링)"
)
selected_project_id = proj_filter_opts[proj_filter]

filtered_events = [e for e in events if e.get("project_id") == selected_project_id] if selected_project_id else events
display_events = filtered_events if selected_project_id else events

# 선택된 프로젝트 정보 표시
if selected_project_id:
    selected_proj = next((p for p in projs if p["id"] == selected_project_id), None)
    if selected_proj:
        col2.info(f"📌 **프로젝트**: {plabel[selected_project_id]}")
else:
    col2.info("전체 송금 일정을 보고 있습니다 (전체 프로젝트 포함)")

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

# 프로젝트별 상세 정보 (프로젝트 선택 시)
if selected_project_id:
    with st.expander("📊 프로젝트 상세 현황", expanded=True):
        sel_events = [e for e in events if e.get("project_id") == selected_project_id]
        if sel_events:
            # 미처리 항목 분류
            in_unpaid = [e for e in sel_events if e["direction"] == "in" and not e["paid"]]
            out_unpaid = [e for e in sel_events if e["direction"] == "out" and not e["paid"]]
            in_paid = [e for e in sel_events if e["direction"] == "in" and e["paid"]]
            out_paid = [e for e in sel_events if e["direction"] == "out" and e["paid"]]
            
            stat_cols = st.columns(4)
            stat_cols[0].metric("✅ 완료 입금", won(sum(e["amount"] for e in in_paid)))
            stat_cols[1].metric("⏳ 미입금", won(sum(e["amount"] for e in in_unpaid)))
            stat_cols[2].metric("✅ 완료 지급", won(sum(e["amount"] for e in out_paid)))
            stat_cols[3].metric("⏳ 미지급", won(sum(e["amount"] for e in out_unpaid)))
            
            # 미처리 항목 목록
            if in_unpaid or out_unpaid:
                st.markdown("**🔔 미처리 항목**")
                pending = in_unpaid + out_unpaid
                for e in sorted(pending, key=lambda x: x.get("due_date")):
                    col_type = "📥" if e["direction"] == "in" else "📤"
                    col_due = (e.get("due_date") or "")[:10]
                    st.write(f"{col_type} {col_due} | {e.get('category')} - {e.get('title') or '-'} | **{won(e['amount'])}**")
        else:
            st.info("이 프로젝트에 등록된 송금 건이 없습니다.")


if not events:
    st.info("등록된 송금 일정이 없습니다. 위 '➕ 송금 일정 추가'로 입력하면 표·달력에 표시됩니다.")
    st.stop()

if "edit_event_id" not in st.session_state:
    st.session_state.edit_event_id = None

# ── 공통 요약 ─────────────────────────────────────────────────
df = pd.DataFrame(filtered_events if selected_project_id else events)
df["due_date"] = pd.to_datetime(df["due_date"], errors="coerce")
inc = df[df["direction"] == "in"]; out = df[df["direction"] == "out"]
proj_label = plabel.get(selected_project_id, "전체") if selected_project_id else "전체"
label_suffix = f" ({proj_label})" if selected_project_id else ""
k = st.columns(4)
k[0].metric(f"받을 돈{label_suffix}", won(inc["amount"].sum()))
k[1].metric(f"나갈 돈{label_suffix}", won(out["amount"].sum()))
k[2].metric(f"미입금(받을){label_suffix}", won(inc[~inc["paid"]]["amount"].sum()))
k[3].metric(f"미지급(나갈){label_suffix}", won(out[~out["paid"]]["amount"].sum()))

mode = st.radio("보기", ["표로 보기", "달력으로 보기"], horizontal=True)

# ── 편집 모달 ─────────────────────────────────────────────────
if st.session_state.edit_event_id:
    edit_event = next((x for x in events if x["id"] == st.session_state.edit_event_id), None)
    if edit_event:
        with st.container(border=True):
            st.markdown("### ✏️ 송금 일정 수정")
            with st.form("edit_event_modal"):
                pf_opts = {"(프로젝트 없음)": None}
                for p in projs:
                    pf_opts[plabel[p["id"]]] = p["id"]
                current_proj = plabel.get(edit_event.get("project_id"), "(프로젝트 없음)") if edit_event.get("project_id") else "(프로젝트 없음)"
                
                cols_form = st.columns([2, 1, 1.2, 1.5])
                pj = cols_form[0].selectbox("🔗 프로젝트", list(pf_opts.keys()), index=list(pf_opts.keys()).index(current_proj))
                direction = cols_form[1].selectbox("구분", ["받을 돈", "나갈 돈"], index=0 if edit_event["direction"] == "in" else 1)
                category = cols_form[2].selectbox("항목", ["선금", "잔금", "인보이스", "실행사 지급", "기타"], index=["선금", "잔금", "인보이스", "실행사 지급", "기타"].index(edit_event.get("category") or "기타"))
                due = cols_form[3].date_input("예정일", value=pd.to_datetime(edit_event.get("due_date"), errors="coerce").date() if edit_event.get("due_date") else date.today())
                
                cols_form2 = st.columns([2.5, 1.2, 1])
                title = cols_form2[0].text_input("메모/제목", value=edit_event.get("title") or "")
                amount = cols_form2[1].number_input("금액", min_value=0, step=100000, value=int(edit_event.get("amount") or 0))
                paid = cols_form2[2].checkbox("완료", value=bool(edit_event.get("paid")))
                
                cols_btn = st.columns([1, 1, 2])
                if cols_btn[0].form_submit_button("💾 저장"):
                    SUPA.table("cash_events").update({
                        "project_id": pf_opts[pj],
                        "direction": "in" if direction == "받을 돈" else "out",
                        "category": category,
                        "title": title,
                        "amount": int(amount),
                        "due_date": due.isoformat(),
                        "paid": paid,
                        "paid_date": due.isoformat() if paid else None,
                    }).eq("id", edit_event["id"]).execute()
                    st.session_state.edit_event_id = None
                    st.toast("수정내용이 저장되었습니다 ✓")
                    st.rerun()
                if cols_btn[1].form_submit_button("취소"):
                    st.session_state.edit_event_id = None
                    st.rerun()
            st.divider()

# ── 표로 보기 ─────────────────────────────────────────────────
if mode == "표로 보기":
    st.markdown("### 📊 송금 일정 목록")
    st.caption(f"표시된 건: {len(display_events)}건 | 프로젝트 ID 기반으로 연결되어 있습니다")
    
    cols = st.columns([1.2, 0.8, 1.4, 1.2, 2, 1, 0.8, 0.8])
    cols[0].markdown("**예정일**")
    cols[1].markdown("**구분**")
    cols[2].markdown("**프로젝트**")
    cols[3].markdown("**항목**")
    cols[4].markdown("**메모/제목**")
    cols[5].markdown("**금액**")
    cols[6].markdown("**수정**")
    cols[7].markdown("**삭제**")
    
    for e in display_events:
        cols = st.columns([1.2, 0.8, 1.4, 1.2, 2, 1, 0.8, 0.8])
        cols[0].write((e.get("due_date") or "")[:10])
        cols[1].write("📥" if e["direction"] == "in" else "📤")
        
        # 프로젝트 선택 (ID 기반)
        proj_opts = {"(프로젝트 없음)": None}
        for p in projs:
            proj_opts[plabel[p["id"]]] = p["id"]
        current_proj_label = plabel.get(e.get("project_id"), "(프로젝트 없음)") if e.get("project_id") else "(프로젝트 없음)"
        current_proj_index = list(proj_opts.keys()).index(current_proj_label) if current_proj_label in list(proj_opts.keys()) else 0
        
        new_proj = cols[2].selectbox(
            "프로젝트",
            list(proj_opts.keys()),
            index=current_proj_index,
            key=f"proj_select_{e['id']}",
            label_visibility="collapsed"
        )
        
        # 프로젝트가 변경되면 즉시 저장
        if new_proj != current_proj_label and proj_opts[new_proj] != e.get("project_id"):
            SUPA.table("cash_events").update({"project_id": proj_opts[new_proj]}).eq("id", e["id"]).execute()
            st.toast(f"프로젝트가 '{new_proj}'로 변경되었습니다 ✓")
            st.rerun()
        
        cols[3].write(e.get("category") or "")
        cols[4].write(e.get("title") or "")
        cols[5].write(won(e["amount"]))
        
        if cols[6].button("✏️", key="edit_" + e["id"], help="편집"):
            st.session_state.edit_event_id = e["id"]
            st.rerun()
        if cols[7].button("🗑️", key="del_" + e["id"], help="삭제"):
            SUPA.table("cash_events").delete().eq("id", e["id"]).execute()
            st.toast("삭제되었습니다 ✓")
            st.rerun()

# ── 달력으로 보기 ─────────────────────────────────────────────
else:
    st.markdown("### 📆 월별 송금 캘린더")
    if "cal_y" not in st.session_state:
        t = date.today(); st.session_state.cal_y = t.year; st.session_state.cal_m = t.month
    
    nav = st.columns([1, 2.5, 1])
    if nav[0].button("◀ 이전 달"):
        m = st.session_state.cal_m - 1; y = st.session_state.cal_y
        if m < 1: m = 12; y -= 1
        st.session_state.cal_m, st.session_state.cal_y = m, y; st.rerun()
    nav[1].markdown(f"<h4 style='text-align:center'>{st.session_state.cal_y}년 {st.session_state.cal_m}월</h4>", unsafe_allow_html=True)
    if nav[2].button("다음 달 ▶"):
        m = st.session_state.cal_m + 1; y = st.session_state.cal_y
        if m > 12: m = 1; y += 1
        st.session_state.cal_m, st.session_state.cal_y = m, y; st.rerun()

    Y, M = st.session_state.cal_y, st.session_state.cal_m
    by_date = {}
    for e in display_events:
        dd = (e.get("due_date") or "")[:10]
        by_date.setdefault(dd, []).append(e)

    head = st.columns(7)
    for i, wd in enumerate(["일", "월", "화", "수", "목", "금", "토"]):
        head[i].markdown(f"<div style='text-align:center;color:#888;font-size:12px;font-weight:600'>{wd}</div>", unsafe_allow_html=True)

    cal = calendar.Calendar(firstweekday=6)  # 일요일 시작
    for week in cal.monthdayscalendar(Y, M):
        cols = st.columns(7)
        for i, day in enumerate(week):
            if day == 0:
                cols[i].markdown("&nbsp;", unsafe_allow_html=True); continue
            iso = date(Y, M, day).isoformat()
            html = f"<div style='border:1px solid #eee;border-radius:6px;padding:6px;min-height:80px;background:#f9f9f9'>"
            html += f"<div style='font-weight:700;font-size:13px;margin-bottom:4px'>{day}</div>"
            for e in by_date.get(iso, []):
                col = "#1aa179" if e["direction"] == "in" else "#d4351c"
                sign = "+" if e["direction"] == "in" else "−"
                chk = "✓" if e["paid"] else ""
                proj_badge = f"<small>[{plabel.get(e.get('project_id'), '?')[:8]}...]</small>"
                html += f"<div style='font-size:10px;color:{col};margin:2px 0'>{sign}{won_short(e['amount'])}{chk} {proj_badge}</div>"
            html += "</div>"
            cols[i].markdown(html, unsafe_allow_html=True)
            
            # 날짜 클릭해서 송금 건 추가 또는 수정
            if cols[i].button("+ 수정", key=f"cal_edit_{iso}", use_container_width=True):
                st.session_state.cal_selected_date = iso
                st.session_state.view_mode = "detail"
                st.rerun()
    
    st.caption("💡 초록 +금액 = 받을 돈 | 빨강 −금액 = 나갈 돈 | ✓ = 완료 | [프로젝트명] = 연결된 프로젝트")
    
    # ── 달력에서 날짜 선택 시 상세 보기 ────────────────────────
    if "cal_selected_date" in st.session_state and st.session_state.get("view_mode") == "detail":
        st.divider()
        selected_date = st.session_state.cal_selected_date
        events_on_date = [e for e in display_events if (e.get("due_date") or "")[:10] == selected_date]
        
        st.markdown(f"### 📅 {selected_date} 송금 현황")
        
        if events_on_date:
            for idx, e in enumerate(events_on_date):
                with st.expander(f"[{e.get('category')}] {e.get('title') or '-'} — {won(e['amount'])}", expanded=(idx==0)):
                    cols_detail = st.columns([2, 1, 1, 1])
                    cols_detail[0].write(f"**프로젝트**: {plabel.get(e.get('project_id'), '(미연결)')}")
                    cols_detail[1].write(f"**상태**: {'완료 ✓' if e['paid'] else '예정'}")
                    cols_detail[2].write(f"**구분**: {'받을 돈 📥' if e['direction'] == 'in' else '나갈 돈 📤'}")
                    
                    if cols_detail[3].button("✏️ 편집", key=f"detail_edit_{e['id']}"):
                        st.session_state.edit_event_id = e["id"]
                        st.rerun()
        else:
            st.info(f"이 날짜에 등록된 송금 일정이 없습니다.")
        
        if st.button("닫기"):
            st.session_state.pop("cal_selected_date", None)
            st.session_state.pop("view_mode", None)
            st.rerun()
