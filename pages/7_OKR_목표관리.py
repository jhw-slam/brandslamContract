import os
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="OKR 목표관리", layout="wide")

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

ADMIN_EMAIL = "jhw@slam-global.com"

# ── 촘촘한 시트형 UI (Notion/Linear/엑셀 느낌) ────────────────
st.markdown("""
<style>
/* 상세보기 expander를 '펼치는 시트 행'처럼 컴팩트하게 */
div[data-testid="stExpander"] {
    border: none !important;
    border-top: none !important;
    border-bottom: 1px solid #eef0f3 !important;
    border-radius: 0 !important;
    margin-bottom: 2px !important;
}
div[data-testid="stExpander"] summary {
    padding: 4px 6px !important;
    min-height: unset !important;
}
div[data-testid="stExpander"] summary:hover { background: #f7f8fa !important; }
div[data-testid="stExpander"] summary p { font-size: 12px !important; color: #9aa0a8 !important; }
div[data-testid="stExpander"] div[data-testid="stExpanderDetails"] { padding-top: 4px !important; }

/* 시트 행 헤더 */
.okr-hdr {
    display: grid; gap: 10px; padding: 6px 8px;
    font-size: 11px; font-weight: 700; color: #8a8f98;
    border-bottom: 1.5px solid #dfe2e8;
    text-transform: uppercase; letter-spacing: .03em;
}
/* 시트 행 본문 */
.okr-row-html {
    display: grid; gap: 10px; padding: 9px 8px;
    align-items: center; font-size: 13.5px;
    border-bottom: 1px solid #f2f3f5;
}
.okr-row-html:hover { background: #fafbfc; }
.okr-title { font-weight: 700; }
.okr-cat { color: #9aa0a8; font-size: 11px; margin-left: 7px; font-weight: 500; }
.okr-num { font-variant-numeric: tabular-nums; color: #444; }
.okr-person { font-weight: 700; font-size: 12.5px; }
</style>
""", unsafe_allow_html=True)

st.title("🎯 OKR 목표관리")
st.caption("OWM → LSP → 브랜드슬램 비전 캐스케이드와 구성원별 OKR · 실시간 DB 연동")

# ── 관리자 확인 (이메일) ────────────────────────────────────
st.session_state.setdefault("user_email", "")
id_c1, id_c2 = st.columns([3, 1.4])
with id_c2:
    st.session_state["user_email"] = st.text_input(
        "내 이메일", value=st.session_state["user_email"],
        placeholder="jhw@slam-global.com", help="관리자 이메일로 입력하면 확정된 목표도 수정할 수 있어요."
    )
is_admin = st.session_state["user_email"].strip().lower() == ADMIN_EMAIL
with id_c1:
    if is_admin:
        st.success("🔑 관리자 모드 — 확정된 목표를 포함해 모든 항목을 수정할 수 있습니다.")
    else:
        st.caption("일반 참여자 모드 — 확정 전 목표는 자유롭게 관리하고, 확정 이후 변경은 관리자만 가능합니다.")

st.divider()

# ── 비전 캐스케이드 ─────────────────────────────────────────────
st.subheader("비전 캐스케이드")

TIERS = [
    {
        "name": "OWM", "sub": "그룹 비전",
        "vision": "입점 브랜드사의 성장 + OWM 각 매장 매출의 비약적 성장.\n\n미션: 이를 위한 전 프로세스의 개발과 효율화.",
        "goal": "입점 브랜드·매장 매출 신장률 **극대화** (구체 목표치 확정 필요)",
    },
    {
        "name": "LSP", "sub": "라이프스타일프로젝트",
        "vision": "옵티마 약국체인 보유. OWM 자회사로서 그룹 비전을 실행 가능한 사업 단위로 전환.",
        "goal": "기업가치 **약 1,000억** → 내년 **2,000~3,000억**",
    },
    {
        "name": "브랜드슬램", "sub": "실행 조직",
        "vision": "미국·중국 SNS 인플루언서 마케팅 + 현지 계정 운영, AX 자동화 파이프라인, 유통·라이브커머스 확장.",
        "goal": "연 **120억** 예산 집행 + 전략 10 / 준전략 50~70 브랜드 유지 + 노스스타 KPI(매장·브랜드 매출 신장률, 콘텐츠 종합평가 성장률) 달성",
    },
]

for t in TIERS:
    with st.container(border=True):
        c1, c2, c3 = st.columns([1, 2.3, 2.3])
        with c1:
            st.markdown(f"**{t['name']}**")
            st.caption(t["sub"])
        with c2:
            st.markdown("**비전 · 미션**")
            st.write(t["vision"])
        with c3:
            st.markdown("**결과적 목표**")
            st.markdown(t["goal"])

st.write("")
n1, n2, n3 = st.columns(3)
n1.metric("연간 OWM 마케팅 예산", "120억")
n2.metric("월 집행 목표", "10억 / 월")
n3.metric("전략 / 준전략 브랜드", "10 / 50~70")

st.divider()

# ── 데이터 로드 ───────────────────────────────────────────────
PEOPLE_ORDER = ["양혜준", "김선재", "정다영", "구정회", "박솔 이사", "미국 리드"]
CADENCE_LABEL = {"weekly": "주간", "monthly": "월간", "quarterly": "분기", "once": "1회성"}
CADENCE_KEYS = ["monthly", "weekly", "quarterly", "once"]
BADGE = {
    "ok":    ("#e9f7ee", "#15803d"),
    "watch": ("#fdf2e0", "#b45309"),
    "late":  ("#fdecec", "#dc2626"),
    "blue":  ("#eaf0fd", "#2451c4"),
}

@st.cache_data(ttl=10)
def load_org():
    rows = SUPA.table("okr_org").select("*").execute().data
    return {r["person"]: r for r in rows}

@st.cache_data(ttl=10)
def load_items():
    return SUPA.table("okr_items").select("*").order("created_at").execute().data

def refresh():
    load_org.clear(); load_items.clear(); st.rerun()

# ── 기간/페이스 계산 (데이터 로드보다 먼저 정의 — 롤오버에서 사용) ──
def start_of_week(d): return d - timedelta(days=d.weekday())
def end_of_week(d): return start_of_week(d) + timedelta(days=6)
def start_of_month(d): return d.replace(day=1)
def end_of_month(d):
    nxt = d.replace(day=28) + timedelta(days=4)
    return nxt.replace(day=1) - timedelta(days=1)
def start_of_quarter(d):
    q = (d.month - 1) // 3
    return date(d.year, q * 3 + 1, 1)
def end_of_quarter(d):
    q = (d.month - 1) // 3
    if q == 3:
        return date(d.year, 12, 31)
    return date(d.year, (q + 1) * 3 + 1, 1) - timedelta(days=1)

def period_bounds(cadence, ref):
    if cadence == "weekly": return start_of_week(ref), end_of_week(ref)
    if cadence == "monthly": return start_of_month(ref), end_of_month(ref)
    if cadence == "quarterly": return start_of_quarter(ref), end_of_quarter(ref)
    return ref, ref

def period_for(item, ref=None):
    ref = ref or date.today()
    c = item["cadence"]
    if c in ("weekly", "monthly", "quarterly"):
        return period_bounds(c, ref)
    d = item.get("once_date")
    if d:
        d = date.fromisoformat(d) if isinstance(d, str) else d
        return d, d
    return ref, ref

def _parse_d(v):
    if v is None: return None
    return date.fromisoformat(v) if isinstance(v, str) else v

def is_achieved(item):
    target = float(item.get("target_qty") or 0)
    progress = float(item.get("progress") or 0)
    return target > 0 and progress >= target

def close_one_period(it):
    """승인된 기간을 okr_period_log에 스냅샷으로 박제하고 다음 기간으로 리셋한다."""
    today = date.today()
    stored_end = _parse_d(it.get("period_end")) or today
    old_start = _parse_d(it.get("period_start")) or stored_end
    SUPA.table("okr_period_log").insert({
        "person": it["person"], "category": it["category"], "title": it["title"],
        "cadence": it["cadence"], "period_start": old_start.isoformat(), "period_end": stored_end.isoformat(),
        "target_qty": it["target_qty"], "progress": it["progress"],
        "achieved": is_achieved(it),
    }).execute()
    new_start, new_end = period_for(it, today)
    new_target = it.get("next_target_qty") if it.get("next_target_qty") is not None else it["target_qty"]
    new_progress = it["progress"] if it.get("cumulative") else 0
    new_ratio = (new_progress / new_target) if new_target else 0
    SUPA.table("okr_items").update({
        "target_qty": new_target, "progress": new_progress, "next_target_qty": None,
        "period_start": new_start.isoformat(), "period_end": new_end.isoformat(),
        "confirmed": False, "confirmed_at": None, "needs_clarification": False,
        "closing_submitted": False, "closing_report": None, "closing_submitted_at": None,
        "closing_approved": False, "closing_approved_at": None,
        "achieved_at": (today.isoformat() if new_ratio >= 1 else None),
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", it["id"]).execute()

def run_period_rollover(items):
    """기간이 끝났어도 '마감 승인(closing_approved)'이 되기 전까지는 절대 자동으로 넘어가지 않는다.
    실제 마감은 담당자 리포트 제출 → 상사(관리자) 승인이 있어야만 발생한다.
    이 함수는 이미 승인된 건만 정리(archive+reset)하고, period_start/end가 비어 있는 신규 항목만 초기화한다."""
    today = date.today()
    rolled = False
    for it in items:
        if it["cadence"] not in ("weekly", "monthly", "quarterly"):
            continue
        stored_end = _parse_d(it.get("period_end"))
        if stored_end is None:
            start, end = period_for(it, today)
            SUPA.table("okr_items").update({"period_start": start.isoformat(), "period_end": end.isoformat()}).eq("id", it["id"]).execute()
            continue
        if stored_end >= today:
            continue  # 아직 현재 기간 — 손대지 않음
        if not it.get("closing_approved"):
            continue  # 기간은 지났지만 아직 마감 승인 전 — '마감 PT 대기' 상태로 그대로 둔다
        close_one_period(it)
        rolled = True
    return rolled

def is_overdue_for_closing(item):
    if item["cadence"] not in ("weekly", "monthly", "quarterly"):
        return False
    end = _parse_d(item.get("period_end"))
    return bool(end) and end < date.today() and not item.get("closing_approved")

ORG = load_org()
_raw_items = load_items()
if run_period_rollover(_raw_items):
    load_items.clear()
ITEMS = load_items()

def pace_status(item, today=None):
    today = today or date.today()
    target = float(item.get("target_qty") or 0)
    progress = float(item.get("progress") or 0)
    if target <= 0:
        return ("blue", "수치 미확정")
    start, end = period_for(item, today)
    total = max(1, (end - start).days + 1)
    elapsed = min(total, max(0, (today - start).days + 1))
    expected_ratio = elapsed / total
    actual_ratio = progress / target
    if actual_ratio >= 1:
        return ("ok", "달성")
    if today > end:
        return ("late", "기한 초과")
    if actual_ratio < expected_ratio * 0.8:
        return ("late", "지연")
    if actual_ratio < expected_ratio * 0.95:
        return ("watch", "주의")
    return ("ok", "정상 진행")

def deadline_label(item, today=None):
    _, end = period_for(item, today or date.today())
    return end.strftime("%m/%d")

def progress_pct(item):
    t = float(item.get("target_qty") or 0)
    if t <= 0: return 0
    return max(0, min(100, round(float(item.get("progress") or 0) / t * 100)))

def badge_html(key, label, extra=""):
    bg, fg = BADGE[key]
    return (f"<span style='background:{bg};color:{fg};padding:2px 9px;border-radius:20px;"
            f"font-size:11px;font-weight:700;white-space:nowrap'>{label}</span>{extra}")

def bar_html(pct, key):
    _, fg = BADGE[key]
    return (f"<div style='background:#eceef1;border-radius:4px;height:6px;width:90px'>"
            f"<div style='background:{fg};width:{pct}%;height:100%;border-radius:4px'></div></div>"
            f"<span style='font-size:11px;color:#888'>{pct}%</span>")

def items_of(person):
    return [it for it in ITEMS if it["person"] == person]

# ── 담당자 선택 ───────────────────────────────────────────────
st.subheader("개인별 OKR · KPI 관리")
sel_col, _ = st.columns([2, 5])
selected = sel_col.selectbox("담당자", ["전체 보기"] + PEOPLE_ORDER, index=1)

# ── 상위 Objective/KR 고정 헤더 ───────────────────────────────
def render_org_card(name):
    org = ORG.get(name, {})
    krs = org.get("krs") or []
    with st.container(border=True):
        pending_tag = "  ·  🕗 채용예정" if org.get("pending") else ""
        st.caption(f"{name} · {org.get('tag','')}{pending_tag}")
        st.markdown(f"**Objective** · {org.get('objective') or '_미입력_'}")
        for i, kr in enumerate(krs, 1):
            warn = "⚠️" in kr
            st.markdown(f"{'⚠️ ' if warn else ''}**KR{i}** · {kr}")
        with st.expander("Objective / KR 수정" + ("" if is_admin else " (관리자 전용)"), expanded=False):
            if not is_admin:
                st.info("Objective/KR 변경은 관리자(jhw@slam-global.com)만 할 수 있어요.")
            else:
                with st.form(f"org_form_{name}"):
                    new_obj = st.text_input("Objective", value=org.get("objective", ""))
                    new_krs_txt = st.text_area("KR (한 줄에 하나씩)", value="\n".join(krs), height=110)
                    new_email = st.text_input("담당자 이메일 (마감 알림 발송용)", value=org.get("email") or "")
                    if st.form_submit_button("저장"):
                        krs_list = [x.strip() for x in new_krs_txt.split("\n") if x.strip()]
                        SUPA.table("okr_org").update(
                            {"objective": new_obj.strip(), "krs": krs_list, "email": new_email.strip(),
                             "updated_at": datetime.utcnow().isoformat()}
                        ).eq("person", name).execute()
                        st.success("저장했습니다."); refresh()

if selected != "전체 보기":
    render_org_card(selected)
else:
    gcols = st.columns(2)
    for i, name in enumerate(PEOPLE_ORDER):
        with gcols[i % 2]:
            org = ORG.get(name, {})
            with st.container(border=True):
                st.caption(f"{name} · {org.get('tag','')}")
                st.markdown(f"**Objective** · {org.get('objective') or '_미입력_'}")
                for j, kr in enumerate(org.get("krs") or [], 1):
                    st.markdown(f"- KR{j} · {kr}")

# ── 요약 카드 ─────────────────────────────────────────────────
scoped = items_of(selected) if selected != "전체 보기" else ITEMS
total = len(scoped)
late_n = sum(1 for it in scoped if pace_status(it)[0] == "late")
watch_n = sum(1 for it in scoped if pace_status(it)[0] == "watch")
confirmed_n = sum(1 for it in scoped if it["confirmed"])
achieved_n = sum(1 for it in scoped if is_achieved(it))
overdue_n = sum(1 for it in scoped if is_overdue_for_closing(it))

m = st.columns(6)
m[0].metric("관리 중 업무", f"{total}건")
m[1].metric("지연", f"{late_n}건", delta="확인 필요" if late_n else None, delta_color="inverse")
m[2].metric("주의", f"{watch_n}건", delta="확인 필요" if watch_n else None, delta_color="inverse")
m[3].metric("확정된 목표", f"{confirmed_n}/{total}")
m[4].metric("🏆 달성", f"{achieved_n}건")
m[5].metric("⏰ 마감 PT 대기", f"{overdue_n}건", delta="승인 필요" if overdue_n else None, delta_color="inverse")

if overdue_n and is_admin:
    st.warning(f"⏰ **마감 PT 승인이 필요한 항목이 {overdue_n}건** 있습니다. '🗓 마감 PT' 탭에서 담당자 리포트를 확인하고 승인해주세요.")
elif overdue_n:
    st.warning(f"⏰ 기간이 끝난 목표 {overdue_n}건이 상사 확인을 기다리고 있어요. '🗓 마감 PT' 탭에서 리포트를 제출해주세요.")

# ── 탭 (탭마다 폼 key가 겹치지 않도록 ctx를 구분해서 넘김) ────
tab_okr, tab_list, tab_cal, tab_late, tab_week, tab_confirmed, tab_achieved, tab_closing, tab_history = st.tabs(
    ["OKR표", "리스트로 보기", "캘린더 보기", "미달성 KPI 보기", "이번주 수량체크", "협의된 목표 보기", "🏆 달성한 KPI", "🗓 마감 PT", "📜 지난 기록"]
)

def build_payload_with_achievement(it, n_qty, n_progress):
    """진행률이 목표를 넘기면 achieved_at 자동 기록, 다시 내려가면 해제."""
    ratio = (n_progress / n_qty) if n_qty > 0 else 0
    achieved_at = it.get("achieved_at")
    if ratio >= 1 and not achieved_at:
        achieved_at = date.today().isoformat()
    elif ratio < 1 and achieved_at:
        achieved_at = None
    return achieved_at

def item_detail_form(it, ctx):
    """ctx: 'okr' | 'late' | 'cal' | 'achieved' — 같은 항목이 여러 탭에 동시에 나와도 폼 key가 겹치지 않게 함."""
    locked = it["confirmed"] and not is_admin
    label = "▾ 상세 · 수정" + (" 🔒" if locked else "")
    recurring = it["cadence"] in ("weekly", "monthly", "quarterly")
    with st.expander(label):
        if locked:
            st.info("🔒 이 목표는 이미 협의·확정되었습니다. 변경은 관리자(jhw@slam-global.com)만 할 수 있어요.")
            st.write(f"목표 {it['target_qty']} {it['unit']} · 진행 {it['progress']} · 마감 {deadline_label(it)}")
            if it.get("note"):
                st.caption(it["note"])
            return

        if recurring and it.get("period_start") and it.get("period_end"):
            st.caption(f"📅 이번 기간 · {it['period_start']} ~ {it['period_end']} (지난 기간은 자동으로 '지난 기록'에 박제되어 수정할 수 없어요)")
        if is_overdue_for_closing(it):
            st.warning("⏰ 이 기간은 이미 끝났습니다. 여기서 숫자는 계속 고칠 수 있지만, **'🗓 마감 PT' 탭에서 리포트 제출 → 관리자 승인**이 있어야 실제로 마감됩니다.")
        if it.get("next_target_qty") is not None:
            st.caption(f"⏭ 다음 기간부터 목표 **{it['next_target_qty']} {it['unit']}**(으)로 바뀔 예정입니다.")

        with st.form(f"edit_{ctx}_{it['id']}"):
            e1, e2, e3, e4 = st.columns(4)
            n_title = e1.text_input("업무명", value=it["title"])
            n_unit = e3.text_input("단위", value=it["unit"] or "건")
            n_cadence = e4.selectbox("주기", CADENCE_KEYS,
                                      index=CADENCE_KEYS.index(it["cadence"]),
                                      format_func=lambda x: CADENCE_LABEL[x])

            if recurring:
                e2.caption("목표 수량 ↓ (적용 시점 선택)")
                n_qty = e2.number_input("새 목표 수량", value=float(it["next_target_qty"] if it.get("next_target_qty") is not None else it["target_qty"]), min_value=0.0, label_visibility="collapsed")
                apply_when = e2.radio("적용 시점", ["이번 기간에 즉시 적용", "다음 기간부터 적용"], horizontal=False, label_visibility="collapsed")
            else:
                n_qty = e2.number_input("목표 수량", value=float(it["target_qty"]), min_value=0.0)
                apply_when = "이번 기간에 즉시 적용"

            p1, p2 = st.columns(2)
            n_progress = p1.number_input("현재 진행 (이번 기간)", value=float(it["progress"]), min_value=0.0)
            n_once = None
            n_cumulative = it.get("cumulative", False)
            if n_cadence == "once":
                default_once = date.fromisoformat(it["once_date"]) if it.get("once_date") else date.today()
                n_once = p2.date_input("마감일(1회성)", value=default_once)
            elif recurring and is_admin:
                n_cumulative = p2.checkbox("누적형 KPI (기간이 바뀌어도 진행 수치를 초기화하지 않음)", value=it.get("cumulative", False))
            else:
                if recurring:
                    p2.caption(("누적형 KPI (관리자만 변경 가능)" if not n_cumulative else "🔁 누적형 — 기간이 바뀌어도 리셋되지 않음"))

            n_note = st.text_area("메모 / 상세 설명", value=it.get("note") or "")

            c1, c2 = st.columns(2)
            if is_admin:
                n_confirmed = c1.checkbox("협의된 목표로 확정 (관리자 전용)", value=it["confirmed"])
            else:
                c1.caption("🔒 확정 여부는 관리자만 바꿀 수 있어요.")
                n_confirmed = it["confirmed"]
            n_clarify = c2.checkbox("💬 설명필요 표시", value=it["needs_clarification"])

            b1, b2, b3 = st.columns([1, 1, 3])
            save = b1.form_submit_button("저장", type="primary")
            delete = b2.form_submit_button("삭제")

            if save:
                immediate = (apply_when == "이번 기간에 즉시 적용")
                new_target_now = n_qty if immediate else it["target_qty"]
                new_next_target = None if immediate else n_qty

                achieved_at = build_payload_with_achievement(it, new_target_now, n_progress)
                payload = {
                    "title": n_title.strip(), "unit": n_unit.strip() or "건",
                    "cadence": n_cadence, "progress": n_progress, "note": n_note,
                    "needs_clarification": n_clarify, "achieved_at": achieved_at,
                    "once_date": n_once.isoformat() if n_once else None,
                    "target_qty": new_target_now, "next_target_qty": new_next_target,
                    "cumulative": n_cumulative,
                    "updated_at": datetime.utcnow().isoformat(),
                }
                if n_cadence != it["cadence"] and n_cadence in ("weekly", "monthly", "quarterly"):
                    ns, ne = period_bounds(n_cadence, date.today())
                    payload["period_start"] = ns.isoformat(); payload["period_end"] = ne.isoformat()
                if is_admin:
                    payload["confirmed"] = n_confirmed
                    if n_confirmed and not it["confirmed"]:
                        payload["confirmed_at"] = date.today().isoformat()
                    elif not n_confirmed:
                        payload["confirmed_at"] = None
                SUPA.table("okr_items").update(payload).eq("id", it["id"]).execute()
                if achieved_at and not it.get("achieved_at"):
                    st.balloons()
                    st.success("🏆 목표 달성! 축하합니다.")
                elif not immediate:
                    st.success(f"저장했습니다. 목표 변경은 다음 기간({CADENCE_LABEL[n_cadence]})부터 적용됩니다.")
                else:
                    st.success("저장했습니다.")
                refresh()
            if delete:
                SUPA.table("okr_items").delete().eq("id", it["id"]).execute()
                st.warning("삭제했습니다."); refresh()

GRID_WITH_PERSON = "0.7fr 3.1fr 0.8fr 1.1fr 1.3fr 0.7fr 1.2fr"
GRID_NO_PERSON = "3.6fr 0.8fr 1.1fr 1.3fr 0.7fr 1.2fr"

def render_header_row(show_person=False):
    cols_def = GRID_WITH_PERSON if show_person else GRID_NO_PERSON
    labels = (["담당자", "업무", "주기", "목표", "진행률", "마감", "상태"] if show_person
              else ["업무", "주기", "목표", "진행률", "마감", "상태"])
    cells = "".join(f"<div>{l}</div>" for l in labels)
    st.markdown(f"<div class='okr-hdr' style='grid-template-columns:{cols_def}'>{cells}</div>", unsafe_allow_html=True)

def render_item_row(it, ctx, show_person=False):
    key, label = pace_status(it)
    pct = progress_pct(it)
    dl = deadline_label(it)
    crown = " 👑" if is_achieved(it) else ""
    extra = " 💬" if it["needs_clarification"] else ""
    qty_str = f"{it['progress']}/{it['target_qty']} {it['unit']}" if it["target_qty"] > 0 else "미확정"
    conf_badge = " " + badge_html("blue", "확정") if it["confirmed"] else ""
    overdue_badge = " " + badge_html("late", "마감PT 대기") if is_overdue_for_closing(it) else ""
    cols_def = GRID_WITH_PERSON if show_person else GRID_NO_PERSON
    person_cell = f"<div class='okr-person'>{it['person']}</div>" if show_person else ""
    html = f"""<div class='okr-row-html' style='grid-template-columns:{cols_def}'>
        {person_cell}
        <div><span class='okr-title'>{it['title']}{crown}</span><span class='okr-cat'>{it['category']}{extra}</span></div>
        <div class='okr-num'>{CADENCE_LABEL[it['cadence']]}</div>
        <div class='okr-num'>{qty_str}</div>
        <div>{bar_html(pct, key)}</div>
        <div class='okr-num'>{dl}</div>
        <div>{badge_html(key, label)}{conf_badge}{overdue_badge}</div>
    </div>"""
    st.markdown(html, unsafe_allow_html=True)
    item_detail_form(it, ctx)

# ── OKR표 ───────────────────────────────────────────────────
with tab_okr:
    people_shown = [selected] if selected != "전체 보기" else PEOPLE_ORDER
    if selected != "전체 보기":
        with st.expander("+ 새 업무 추가"):
            with st.form(f"add_{selected}", clear_on_submit=True):
                a1, a2, a3, a4 = st.columns(4)
                t_title = a1.text_input("업무명")
                t_qty = a2.number_input("수량", min_value=0.0, value=0.0)
                t_unit = a3.text_input("단위", value="건")
                t_cadence = a4.selectbox("주기", CADENCE_KEYS, format_func=lambda x: CADENCE_LABEL[x])
                if st.form_submit_button("추가"):
                    if t_title.strip():
                        payload = {
                            "person": selected, "title": t_title.strip(), "target_qty": t_qty,
                            "unit": t_unit.strip() or "건", "cadence": t_cadence,
                        }
                        if t_cadence in ("weekly", "monthly", "quarterly"):
                            ns, ne = period_bounds(t_cadence, date.today())
                            payload["period_start"] = ns.isoformat(); payload["period_end"] = ne.isoformat()
                        SUPA.table("okr_items").insert(payload).execute()
                        st.success("추가했습니다."); refresh()
                    else:
                        st.error("업무명을 입력해주세요.")
    for p in people_shown:
        p_items = items_of(p)
        if selected == "전체 보기":
            st.markdown(f"#### {p} · {ORG.get(p,{}).get('tag','')}")
        if not p_items:
            st.caption("아직 등록된 업무가 없습니다." + ("" if selected != "전체 보기" else " (위에서 담당자를 선택해 추가하세요)"))
            st.write("")
            continue
        render_header_row(show_person=False)
        for it in p_items:
            render_item_row(it, ctx="okr", show_person=False)

# ── 리스트로 보기 ───────────────────────────────────────────
with tab_list:
    all_items = items_of(selected) if selected != "전체 보기" else ITEMS
    if not all_items:
        st.caption("표시할 항목이 없습니다.")
    else:
        all_items_sorted = sorted(all_items, key=lambda x: (pace_status(x)[0] != "late", pace_status(x)[0] != "watch"))
        rows = []
        for it in all_items_sorted:
            key, label = pace_status(it)
            rows.append({
                "담당자": it["person"], "업무": ("👑 " if is_achieved(it) else "") + it["title"], "카테고리": it["category"],
                "주기": CADENCE_LABEL[it["cadence"]],
                "목표": f"{it['target_qty']} {it['unit']}" if it["target_qty"] > 0 else "미확정",
                "진행": f"{it['progress']} ({progress_pct(it)}%)",
                "마감": deadline_label(it), "상태": label,
                "확정": "✅" if it["confirmed"] else "",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ── 캘린더 보기 ─────────────────────────────────────────────
with tab_cal:
    cal_items = items_of(selected) if selected != "전체 보기" else ITEMS
    today = date.today()
    month_offset = st.session_state.get("okr_cal_offset", 0)
    cnav = st.columns([1, 3, 1])
    if cnav[0].button("‹ 이전달"):
        st.session_state["okr_cal_offset"] = month_offset - 1; st.rerun()
    if cnav[2].button("다음달 ›"):
        st.session_state["okr_cal_offset"] = month_offset + 1; st.rerun()

    base_month = today.month - 1 + month_offset
    base_year = today.year + base_month // 12
    base_month = base_month % 12 + 1
    base = date(base_year, base_month, 1)
    cnav[1].markdown(f"<div style='text-align:center;font-weight:800;font-size:16px'>{base.year}년 {base.month}월</div>", unsafe_allow_html=True)

    grid_start = start_of_week(base)
    grid_end = end_of_week(end_of_month(base))

    day_map = {}       # 마감일 -> 항목들
    crown_map = {}     # 달성일(achieved_at) -> 항목들
    for it in cal_items:
        c = it["cadence"]
        if c == "once":
            d = it.get("once_date")
            if d:
                d = date.fromisoformat(d) if isinstance(d, str) else d
                if grid_start <= d <= grid_end:
                    day_map.setdefault(d, []).append(it)
        else:
            cursor = grid_start
            seen = set()
            while cursor <= grid_end:
                _, end = period_for(it, cursor)
                if grid_start <= end <= grid_end and end not in seen:
                    day_map.setdefault(end, []).append(it)
                    seen.add(end)
                cursor += timedelta(days=7 if c == "weekly" else 30)
        if it.get("achieved_at"):
            ad = date.fromisoformat(it["achieved_at"]) if isinstance(it["achieved_at"], str) else it["achieved_at"]
            if grid_start <= ad <= grid_end:
                crown_map.setdefault(ad, []).append(it)

    dows = ["월", "화", "수", "목", "금", "토", "일"]
    html = "<div style='display:grid;grid-template-columns:repeat(7,1fr);gap:6px'>"
    for dw in dows:
        html += f"<div style='font-size:11px;color:#9ca3af;text-align:center;font-weight:700'>{dw}</div>"
    cursor = grid_start
    while cursor <= grid_end:
        out = cursor.month != base.month
        is_today = cursor == today
        has_crown = cursor in crown_map
        bg = "#fffbea" if has_crown else ("#f7f7f8" if out else "#fff")
        border = "1.5px solid #d4a017" if has_crown else ("1px solid #16181d" if is_today else "1px solid #e7e8ec")
        chips = ""
        if has_crown:
            chips += "<div style='font-size:14px'>👑</div>"
        for it in day_map.get(cursor, [])[:3]:
            k, _ = pace_status(it)
            bg2, fg2 = BADGE[k]
            chips += (f"<div style='background:{bg2};color:{fg2};font-size:10px;padding:2px 5px;"
                      f"border-radius:4px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis'>"
                      f"{it['person']} · {it['title']}</div>")
        extra = len(day_map.get(cursor, [])) - 3
        if extra > 0:
            chips += f"<div style='font-size:10px;color:#9ca3af'>+{extra}건 더</div>"
        html += (f"<div style='background:{bg};border:{border};border-radius:8px;padding:6px;min-height:74px'>"
                 f"<div style='font-size:11px;font-weight:700;color:#6b7280'>{cursor.day}</div>{chips}</div>")
        cursor += timedelta(days=1)
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)
    st.caption("👑 표시된 날짜 = 그날 목표를 달성한 기록이 있어요.")

    st.write("")
    pick = st.date_input("특정 날짜의 마감/달성 항목 보기", value=today)
    day_items = day_map.get(pick, [])
    crown_items = crown_map.get(pick, [])
    if crown_items:
        st.markdown("**🏆 이 날짜에 달성한 목표**")
        for it in crown_items:
            render_item_row(it, ctx="cal_crown", show_person=(selected == "전체 보기"))
    if day_items:
        st.markdown(f"**{pick.strftime('%Y-%m-%d')} 마감 항목**")
        for it in day_items:
            render_item_row(it, ctx="cal", show_person=(selected == "전체 보기"))
    if not day_items and not crown_items:
        st.caption("해당 날짜에 마감·달성 기록이 없습니다.")

# ── 미달성 KPI 보기 ─────────────────────────────────────────
with tab_late:
    scope_items = items_of(selected) if selected != "전체 보기" else ITEMS
    risky = [it for it in scope_items if pace_status(it)[0] in ("late", "watch")]
    if not risky:
        st.success("🎉 현재 지연·주의 항목이 없습니다.")
    else:
        risky.sort(key=lambda x: pace_status(x)[0] != "late")
        for it in risky:
            start, end = period_for(it)
            today = date.today()
            total = max(1, (end - start).days + 1)
            elapsed = min(total, max(0, (today - start).days + 1))
            expected = round(float(it["target_qty"]) * elapsed / total) if it["target_qty"] else 0
            key, label = pace_status(it)
            with st.container(border=True):
                cc = st.columns([3, 2, 1.4])
                cc[0].markdown(f"**{it['person']} · {it['title']}**  \n<span style='font-size:11px;color:#9ca3af'>{it['category']}</span>", unsafe_allow_html=True)
                cc[1].write(f"실제 {it['progress']} / 기대치 ≈ {expected} {it['unit']} (목표 {it['target_qty']})")
                cc[2].markdown(badge_html(key, label), unsafe_allow_html=True)
                item_detail_form(it, ctx="late")

# ── 이번주 수량체크 ─────────────────────────────────────────
with tab_week:
    scope_items = items_of(selected) if selected != "전체 보기" else ITEMS
    weekly_items = [it for it in scope_items if it["cadence"] == "weekly"]
    if not weekly_items:
        st.caption("주간 단위로 관리 중인 업무가 없습니다. OKR표에서 주기를 '주간'으로 등록해보세요.")
    else:
        wk_start = start_of_week(date.today())
        st.caption(f"이번 주 · {wk_start.strftime('%m/%d')} 시작")
        for it in weekly_items:
            key, label = pace_status(it)
            with st.form(f"week_{it['id']}"):
                wc = st.columns([3, 1.2, 1.4, 1])
                wc[0].markdown(f"**{it['person']} · {it['title']}**  \n<span style='font-size:11px;color:#9ca3af'>이번 주 목표 {it['target_qty']} {it['unit']}</span>", unsafe_allow_html=True)
                new_val = wc[1].number_input("진행", value=float(it["progress"]), min_value=0.0, label_visibility="collapsed")
                wc[2].markdown(bar_html(progress_pct(it), key) + " " + badge_html(key, label), unsafe_allow_html=True)
                if wc[3].form_submit_button("저장"):
                    achieved_at = build_payload_with_achievement(it, it["target_qty"], new_val)
                    SUPA.table("okr_items").update(
                        {"progress": new_val, "achieved_at": achieved_at, "updated_at": datetime.utcnow().isoformat()}
                    ).eq("id", it["id"]).execute()
                    st.success("저장했습니다."); refresh()

# ── 협의된 목표 보기 ─────────────────────────────────────────
with tab_confirmed:
    scope_items = items_of(selected) if selected != "전체 보기" else ITEMS
    confirmed_items = [it for it in scope_items if it["confirmed"]]
    if not confirmed_items:
        st.info("아직 '확정'된 목표가 없습니다. 상세보기에서 **협의된 목표로 확정**(관리자 전용)을 켜면 여기 표시됩니다.")
    else:
        rows = [{
            "담당자": it["person"], "업무": it["title"], "주기": CADENCE_LABEL[it["cadence"]],
            "목표": f"{it['target_qty']} {it['unit']}", "마감": deadline_label(it),
            "확정일": it.get("confirmed_at") or "-",
        } for it in confirmed_items]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if not is_admin:
            st.caption("🔒 확정된 목표의 변경은 관리자(jhw@slam-global.com)만 할 수 있어요.")

# ── 🏆 달성한 KPI ───────────────────────────────────────────
with tab_achieved:
    scope_items = items_of(selected) if selected != "전체 보기" else ITEMS
    achieved_items = [it for it in scope_items if is_achieved(it)]
    achieved_items.sort(key=lambda x: x.get("achieved_at") or "", reverse=True)
    if not achieved_items:
        st.caption("아직 목표를 달성한 항목이 없습니다. 진행률이 목표치에 도달하면 여기 자동으로 올라오고, 캘린더에도 👑 표시가 붙어요.")
    else:
        st.markdown(
            "<div style='background:linear-gradient(135deg,#fffbea,#fef3c7);border:1px solid #f2e0b8;"
            "border-radius:12px;padding:14px 18px;margin-bottom:14px;font-weight:700;color:#92400e'>"
            f"🏆 지금까지 {len(achieved_items)}개의 목표를 달성했습니다 — 명예의 전당</div>",
            unsafe_allow_html=True
        )
        for it in achieved_items:
            with st.container(border=True):
                ac = st.columns([3, 2, 1.4])
                ac[0].markdown(f"👑 **{it['person']} · {it['title']}**  \n<span style='font-size:11px;color:#9ca3af'>{it['category']}</span>", unsafe_allow_html=True)
                ac[1].write(f"{it['target_qty']} {it['unit']} 달성 · {CADENCE_LABEL[it['cadence']]}")
                ac[2].markdown(f"<span style='background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:800'>🏆 {it.get('achieved_at') or ''}</span>", unsafe_allow_html=True)
                item_detail_form(it, ctx="achieved")

# ── 🗓 마감 PT (담당자 리포트 제출 → 관리자 승인 → 그때 비로소 마감) ──
with tab_closing:
    scope_items = items_of(selected) if selected != "전체 보기" else ITEMS
    overdue_items = [it for it in scope_items if is_overdue_for_closing(it)]
    st.caption("기간이 끝난 목표는 여기서 리포트를 제출하고, 관리자가 실제 성과를 확인·승인해야 비로소 마감됩니다. "
               "승인 전까지는 숫자를 계속 고칠 수 있어요 (면담하면서 같이 확인하고 조정 가능).")
    if not overdue_items:
        st.success("🎉 마감 PT 대기 중인 항목이 없습니다.")
    else:
        for it in overdue_items:
            with st.container(border=True):
                hc = st.columns([3, 1.6, 1.4])
                hc[0].markdown(f"**{it['person']} · {it['title']}**  \n<span style='font-size:11px;color:#9ca3af'>{it['category']} · 기간 {it.get('period_start')} ~ {it.get('period_end')}</span>", unsafe_allow_html=True)
                hc[1].write(f"실적 {it['progress']} / 목표 {it['target_qty']} {it['unit']}")
                if it["closing_submitted"]:
                    hc[2].markdown(badge_html("watch", "제출됨 · 승인 대기"), unsafe_allow_html=True)
                else:
                    hc[2].markdown(badge_html("late", "리포트 미제출"), unsafe_allow_html=True)

                with st.form(f"closing_{it['id']}"):
                    n_progress = st.number_input("최종 실적 (필요하면 면담하면서 조정)", value=float(it["progress"]), min_value=0.0)
                    n_report = st.text_area("이번 기간 리포트 (무엇을 했는지 간단히)", value=it.get("closing_report") or "",
                                             placeholder="예: 목표 50건 중 42건 완료. 나머지는 다음주로 이월...")
                    b1, b2 = st.columns(2)
                    submit_report = b1.form_submit_button("📝 마감 요청 제출 (상사 확인 요청)")
                    approve = b2.form_submit_button("✅ 마감 승인 (관리자 전용)", type="primary", disabled=not is_admin)

                    if submit_report:
                        SUPA.table("okr_items").update({
                            "progress": n_progress, "closing_report": n_report,
                            "closing_submitted": True, "closing_submitted_at": datetime.utcnow().isoformat(),
                            "updated_at": datetime.utcnow().isoformat(),
                        }).eq("id", it["id"]).execute()
                        st.success("제출했습니다. 관리자 승인을 기다려주세요."); refresh()

                    if approve and is_admin:
                        # 승인 직전 최종 실적을 반영한 뒤, 그 스냅샷으로 즉시 마감 처리
                        SUPA.table("okr_items").update({
                            "progress": n_progress, "closing_report": n_report,
                            "closing_approved": True, "closing_approved_at": datetime.utcnow().isoformat(),
                            "updated_at": datetime.utcnow().isoformat(),
                        }).eq("id", it["id"]).execute()
                        it_for_close = dict(it); it_for_close["progress"] = n_progress
                        close_one_period(it_for_close)
                        st.success("✅ 마감 승인 완료 — 지난 기록으로 이동하고 다음 기간이 시작됩니다.")
                        st.balloons()
                        refresh()


with tab_history:
    q = SUPA.table("okr_period_log").select("*").order("period_end", desc=True).limit(300)
    if selected != "전체 보기":
        q = q.eq("person", selected)
    hist_rows = q.execute().data
    if not hist_rows:
        st.caption("아직 마감된 기간이 없습니다. 주간/월간/분기 목표의 기간이 지나면 여기에 자동으로 기록이 쌓입니다 (수정 불가 — 순수 기록용).")
    else:
        st.caption("지난 기간의 목표·실적 스냅샷입니다. 이미 지나간 기록이라 여기서는 수정할 수 없어요.")
        rows = [{
            "담당자": r["person"], "업무": ("👑 " if r["achieved"] else "") + r["title"],
            "주기": CADENCE_LABEL.get(r["cadence"], r["cadence"]),
            "기간": f"{r['period_start']} ~ {r['period_end']}",
            "목표": f"{r['target_qty']}", "실적": f"{r['progress']}",
            "달성": "✅" if r["achieved"] else "",
        } for r in hist_rows]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.divider()
st.caption("브랜드슬램 내부 참고용 · Supabase(okr_org / okr_items) 실시간 연동 · 확정 목표는 관리자만 수정 가능")
