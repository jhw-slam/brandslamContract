import os
from datetime import date

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="OKR 다듬기", layout="wide")

# ── 앱 공통 비밀번호 게이트 ──
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ── 재무캘린더와 같은 방식의 전용 게이트 (장현우 전용) ──
FINANCE_ADMIN_EMAIL = "jhw@slam-global.com"
FINANCE_PASSWORD = os.environ.get("FINANCE_PASSWORD")

st.title("🎯 OKR 다듬기")
st.caption("직원들이 남긴 데일리 기록을 보면서, 그걸 팀 OKR로 정리·반영하는 화면입니다. (장현우 전용)")

if FINANCE_PASSWORD and not st.session_state.get("finance_ok"):
    st.warning("🔒 이 페이지는 장현우 전용입니다.")
    fc1, fc2 = st.columns(2)
    email_in = fc1.text_input("이메일", placeholder=FINANCE_ADMIN_EMAIL)
    pw_in = fc2.text_input("비밀번호", type="password")
    if st.button("입장"):
        if email_in.strip().lower() == FINANCE_ADMIN_EMAIL and pw_in == FINANCE_PASSWORD:
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


@st.cache_data(ttl=20)
def load_logs():
    return SUPA.table("daily_activity_log").select("*").order("created_at", desc=True).execute().data


@st.cache_data(ttl=20)
def load_okr():
    org = SUPA.table("okr_org").select("*").order("person").execute().data
    items = SUPA.table("okr_items").select("*").order("person").execute().data
    return org, items


def refresh():
    load_logs.clear()
    load_okr.clear()
    st.rerun()


logs = load_logs()
okr_org_data, okr_items_data = load_okr()
people = sorted(set([o["person"] for o in okr_org_data] + [lg["staff_name"] for lg in logs]))

st.divider()

# ══════════════════════════════════════════════════════════
# 📥 직원 데일리 기록 검토
# ══════════════════════════════════════════════════════════
st.subheader("📥 직원 데일리 기록 검토")
show_reviewed = st.checkbox("검토완료 건도 보기", value=False)
person_filter = st.selectbox("사람 필터", ["전체"] + people)

view_logs = logs if show_reviewed else [lg for lg in logs if not lg.get("reviewed")]
if person_filter != "전체":
    view_logs = [lg for lg in view_logs if lg["staff_name"] == person_filter]

st.caption(f"{len(view_logs)}건 (미검토 우선)")

for lg in view_logs:
    with st.container(border=True):
        when = lg["created_at"][:16].replace("T", " ")
        badge = "✅ 검토완료" if lg.get("reviewed") else "🟡 미검토"
        st.markdown(f"**{lg['staff_name']}** · {when} · {badge}")
        st.write(lg["note"])
        if lg.get("link_url"):
            st.caption(f"🔗 [참고 링크]({lg['link_url']})")
        if lg.get("attachment_url"):
            st.caption(f"📎 [첨부파일]({lg['attachment_url']})")
        if not lg.get("reviewed"):
            if st.button("✅ 검토완료로 표시", key=f"review_{lg['id']}"):
                SUPA.table("daily_activity_log").update({"reviewed": True}).eq("id", lg["id"]).execute()
                refresh()

st.divider()

# ══════════════════════════════════════════════════════════
# 🎯 사람별 OKR 정리
# ══════════════════════════════════════════════════════════
st.subheader("🎯 사람별 OKR 정리")
st.caption("위 기록들을 보고, 이 사람의 OKR 진행률/확정 여부를 여기서 바로 업데이트할 수 있어요. (Objective/KR 문구 자체를 새로 쓰려면 기존 OKR 페이지를 이용하세요)")

target_person = st.selectbox("OKR을 다듬을 사람", people, key="okr_target_person")

org_row = next((o for o in okr_org_data if o["person"] == target_person), None)
if org_row:
    with st.container(border=True):
        st.markdown(f"**🎯 Objective:** {org_row.get('objective') or '-'}")
        for kr in (org_row.get("krs") or []):
            st.markdown(f"- {kr}")
else:
    st.caption("이 사람의 org-level Objective가 아직 없습니다 (기존 OKR 페이지에서 추가해주세요).")

person_items = [it for it in okr_items_data if it["person"] == target_person]
if not person_items:
    st.caption("세부 KR 항목이 없습니다.")
else:
    for it in person_items:
        with st.container(border=True):
            try:
                target_qty = float(it.get("target_qty") or 0)
                progress = float(it.get("progress") or 0)
            except (TypeError, ValueError):
                target_qty, progress = 0.0, 0.0
            st.markdown(f"**[{it.get('category') or '미분류'}] {it['title']}**")
            pc1, pc2, pc3 = st.columns([1.5, 1, 1])
            new_progress = pc1.number_input(
                f"진행 ({it.get('unit') or ''})", value=progress, step=1.0,
                key=f"prog_{it['id']}", label_visibility="visible",
            )
            new_confirmed = pc2.checkbox("확정", value=bool(it.get("confirmed")), key=f"conf_{it['id']}")
            if target_qty > 0:
                pc3.progress(min(new_progress / target_qty, 1.0), text=f"{new_progress:g}/{target_qty:g}")
            if st.button("저장", key=f"okrsave_{it['id']}"):
                update_payload = {"progress": new_progress, "confirmed": new_confirmed}
                if new_confirmed and not it.get("confirmed_at"):
                    update_payload["confirmed_at"] = date.today().isoformat()
                SUPA.table("okr_items").update(update_payload).eq("id", it["id"]).execute()
                st.success("저장 완료")
                refresh()
