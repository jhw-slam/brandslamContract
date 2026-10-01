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

REACTION_OPTS = ["👍", "🔥", "💯", "🙌", "💬"]

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
        if lg.get("manager_feedback"):
            st.success(f"💬 이미 남긴 피드백: {lg.get('manager_reaction') or ''} {lg['manager_feedback']}")

        with st.expander("💬 피드백/칭찬 남기기 (직원 화면에 바로 보여요)"):
            fc1, fc2 = st.columns([1, 4])
            reaction = fc1.selectbox("반응", REACTION_OPTS, key=f"reaction_{lg['id']}", label_visibility="collapsed")
            feedback_text = fc2.text_input(
                "코멘트", value=lg.get("manager_feedback") or "", placeholder="잘했어요! / 이 부분은 이렇게 해볼까요?",
                key=f"feedback_{lg['id']}", label_visibility="collapsed",
            )
            if st.button("전송", key=f"sendfb_{lg['id']}"):
                SUPA.table("daily_activity_log").update({
                    "manager_feedback": feedback_text.strip() or None, "manager_reaction": reaction,
                }).eq("id", lg["id"]).execute()
                st.success("피드백 전송 완료")
                refresh()

        bc1, bc2 = st.columns(2)
        if not lg.get("reviewed"):
            if bc1.button("✅ 검토완료로 표시", key=f"review_{lg['id']}", use_container_width=True):
                SUPA.table("daily_activity_log").update({"reviewed": True}).eq("id", lg["id"]).execute()
                refresh()
        if lg.get("admin_signed"):
            bc2.caption(f"✍️ 승인 서명됨 ({lg['admin_signed_at'][:10]})")
        else:
            if bc2.button("✍️ 완료 승인(서명)", key=f"sign_{lg['id']}", use_container_width=True,
                          help="서명하면 reviewed로도 표시되고, 이 보고가 AI로 매칭했던 OKR 항목이 있으면 자동으로 '달성완료' 처리됩니다."):
                SUPA.table("daily_activity_log").update({
                    "admin_signed": True, "admin_signed_at": pd.Timestamp.now(tz="UTC").isoformat(),
                    "reviewed": True,
                }).eq("id", lg["id"]).execute()
                if lg.get("ai_matched_item_id"):
                    SUPA.table("okr_items").update({
                        "confirmed": True, "last_checkin_at": date.today().isoformat(),
                    }).eq("id", lg["ai_matched_item_id"]).execute()
                    st.success("승인 완료 — 연결된 OKR 항목도 자동으로 달성완료 처리했어요.")
                else:
                    st.success("승인 완료")
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

st.info(
    "🎯 **OKR**(시한부 도전과제, 이번 분기 안에 끝낼 것, 60~70% 달성이 정상)과 "
    "📊 **KPI**(상시 추적하는 건강지표, '반복업무'로 등록, 끝없이 계속 체크)를 분리해서 관리합니다. "
    "— Google/2025 HR 트렌드에서 권장하는 방식이에요."
)

person_items = [it for it in okr_items_data if it["person"] == target_person]


def _item_editor(it, key_prefix):
    with st.container(border=True):
        try:
            target_qty = float(it.get("target_qty") or 0)
            progress = float(it.get("progress") or 0)
        except (TypeError, ValueError):
            target_qty, progress = 0.0, 0.0

        last_checkin = it.get("last_checkin_at")
        staleness = ""
        if last_checkin:
            days = (pd.Timestamp.now(tz="UTC") - pd.Timestamp(last_checkin)).days
            if days >= 7:
                staleness = f" · ⚠️ {days}일간 체크인 없음"
        else:
            staleness = " · ⚠️ 체크인 기록 없음"

        with st.expander(f"{'✅' if it.get('confirmed') else '🔲'} [{it.get('category') or '미분류'}] {it['title']}{staleness}"):
            ec1, ec2 = st.columns(2)
            new_category = ec1.text_input("카테고리", value=it.get("category") or "", key=f"{key_prefix}_cat_{it['id']}")
            new_title = ec2.text_input("제목", value=it["title"], key=f"{key_prefix}_title_{it['id']}")

            pc1, pc2, pc3 = st.columns(3)
            new_target = pc1.number_input("목표치", value=target_qty, step=1.0, key=f"{key_prefix}_target_{it['id']}")
            new_unit = pc2.text_input("단위", value=it.get("unit") or "", key=f"{key_prefix}_unit_{it['id']}")
            new_progress = pc3.number_input("현재 진행", value=progress, step=1.0, key=f"{key_prefix}_prog_{it['id']}")

            if new_target > 0:
                st.progress(min(new_progress / new_target, 1.0), text=f"{new_progress:g}/{new_target:g}{new_unit}")

            cc1, cc2, cc3, cc4 = st.columns(4)
            new_confirmed = cc1.checkbox("✅ 달성완료", value=bool(it.get("confirmed")), key=f"{key_prefix}_conf_{it['id']}")
            new_recurring = cc2.checkbox("🔁 반복업무(KPI)로 등록", value=bool(it.get("is_recurring")), key=f"{key_prefix}_rec_{it['id']}")
            save_clicked = cc3.button("💾 저장", key=f"{key_prefix}_save_{it['id']}", use_container_width=True)
            delete_clicked = cc4.button("🗑️ 삭제", key=f"{key_prefix}_del_{it['id']}", use_container_width=True)

            if save_clicked:
                update_payload = {
                    "category": new_category or None, "title": new_title,
                    "target_qty": new_target, "unit": new_unit or None, "progress": new_progress,
                    "confirmed": new_confirmed, "is_recurring": new_recurring,
                    "last_checkin_at": date.today().isoformat(),
                }
                if new_confirmed and not it.get("confirmed_at"):
                    update_payload["confirmed_at"] = date.today().isoformat()
                SUPA.table("okr_items").update(update_payload).eq("id", it["id"]).execute()
                st.success("저장 완료 (체크인 시각도 갱신됨)")
                refresh()
            if delete_clicked:
                SUPA.table("okr_items").delete().eq("id", it["id"]).execute()
                st.success("삭제 완료")
                refresh()


okr_only = [it for it in person_items if not it.get("is_recurring")]
kpi_only = [it for it in person_items if it.get("is_recurring")]

st.markdown(f"**🎯 이번 사이클 OKR 세부항목 ({len(okr_only)}개)**")
if not okr_only:
    st.caption("없음")
for it in okr_only:
    _item_editor(it, "okr")

st.markdown(f"**📊 상시 추적 KPI ({len(kpi_only)}개)**")
if not kpi_only:
    st.caption("없음")
for it in kpi_only:
    _item_editor(it, "kpi")

with st.expander("➕ 새 항목 추가"):
    with st.form(f"add_item_form_{target_person}", clear_on_submit=True):
        nc1, nc2 = st.columns(2)
        new_item_category = nc1.text_input("카테고리")
        new_item_title = nc2.text_input("제목 *")
        nc3, nc4, nc5 = st.columns(3)
        new_item_target = nc3.number_input("목표치", min_value=0.0, step=1.0)
        new_item_unit = nc4.text_input("단위")
        new_item_is_kpi = nc5.checkbox("🔁 반복업무(KPI)로 등록")
        add_submitted = st.form_submit_button("추가", type="primary")
    if add_submitted:
        if not new_item_title.strip():
            st.error("제목은 꼭 입력해주세요.")
        else:
            SUPA.table("okr_items").insert({
                "person": target_person, "category": new_item_category.strip() or None,
                "title": new_item_title.strip(), "target_qty": new_item_target,
                "unit": new_item_unit.strip() or None, "progress": 0,
                "is_recurring": new_item_is_kpi, "confirmed": False,
                "cadence": "monthly" if new_item_is_kpi else "once",
            }).execute()
            st.success("추가 완료")
            refresh()
