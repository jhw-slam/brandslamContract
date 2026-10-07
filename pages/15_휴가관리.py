import os
import calendar
from datetime import date, datetime, timedelta, timezone

import streamlit as st
from supabase import create_client

st.set_page_config(page_title="휴가 관리", layout="wide")

# ── 레이어 1: 앱 공통 비밀번호 게이트 (다른 페이지와 동일) ──────────
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

# ── 레이어 2: 대표 전용 게이트 (재무캘린더와 같은 이메일+비밀번호, 같은 로그인 상태 공유) ──
ADMIN_EMAIL = "jhw@slam-global.com"
FINANCE_PASSWORD = os.environ.get("FINANCE_PASSWORD")

st.title("🏖️ 휴가 관리")
st.caption("직원 휴가의 입사일·연간 부여일수와 휴가 기록을 고치는 화면 — 대표 전용")

# 재무캘린더는 비밀번호 변수가 없으면 그냥 열리지만, 직원 기록을 고치는 화면이라 여기서는 반대로 잠가둔다.
if not FINANCE_PASSWORD:
    st.error("🔒 FINANCE_PASSWORD 환경변수가 설정되어 있지 않아 이 페이지를 열 수 없습니다. (Railway Variables에 추가해주세요)")
    st.stop()

if not st.session_state.get("finance_ok"):
    st.warning("🔒 이 페이지는 장현우 전용입니다. 이메일과 비밀번호를 입력해주세요.")
    gc1, gc2 = st.columns(2)
    email_in = gc1.text_input("이메일", placeholder=ADMIN_EMAIL)
    pw_in = gc2.text_input("비밀번호", type="password")
    if st.button("휴가 관리 입장"):
        if email_in.strip().lower() == ADMIN_EMAIL and pw_in == FINANCE_PASSWORD:
            st.session_state.finance_ok = True
            st.rerun()
        else:
            st.error("이메일 또는 비밀번호가 올바르지 않습니다.")
    st.stop()

if st.columns([6, 1])[1].button("🔒 잠그기"):
    st.session_state.finance_ok = False
    st.rerun()


# ── Supabase 연결 ──────────────────────────────────────────────
@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()


# ── 휴가 계산 규칙 ─────────────────────────────────────────────
# ⚠️ 아래 상수·함수(_leave_balance 등)는 직원앱(brandslamteamspace/app.py)의 휴가 화면과 "같은 규칙"입니다.
#    직원앱 쪽 계산을 바꾸면 여기도 같이 바꿔야 직원이 보는 잔여와 대표님이 보는 잔여가 어긋나지 않아요.
LEAVE_HOURS = {"연차": 8, "반차": 4, "반반차": 2}  # 1일 = 8시간, 반차 = 4시간, 반반차 = 2시간
LEAVE_SLOTS = {"반차": ["오전", "오후"], "반반차": ["오전 앞", "오전 뒤", "오후 앞", "오후 뒤"]}
_SLOT_QUARTERS = {"오전": {1, 2}, "오후": {3, 4}, "오전 앞": {1}, "오전 뒤": {2}, "오후 앞": {3}, "오후 뒤": {4}}
_WEEKDAY_KO = "월화수목금토일"


def _leave_quarters(kind, slot):
    """하루를 4등분(2시간씩)했을 때 이 휴가가 차지하는 칸. 같은 날 겹치는지 확인할 때 쓴다."""
    return {1, 2, 3, 4} if kind == "연차" else set(_SLOT_QUARTERS.get(slot or "", set()))


def _fmt_days(hours):
    """시간 → 일수 문자열(소수점 활용). 94시간 → '11.75', 120시간 → '15', 2시간 → '0.25'"""
    return f"{hours / 8:.2f}".rstrip("0").rstrip(".")


def _add_months(d, n):
    y, m = divmod(d.year * 12 + (d.month - 1) + n, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _full_months(hire, today):
    """입사일부터 오늘까지 꽉 채운 개월 수."""
    m = (today.year - hire.year) * 12 + (today.month - hire.month) - (1 if today.day < hire.day else 0)
    return max(0, m)


def _leave_d(v):
    return v if isinstance(v, date) and not isinstance(v, datetime) else date.fromisoformat(str(v)[:10])


def _leave_balance(hire, annual_days, leaves, today):
    """잔여 휴가 계산. leaves = 취소되지 않은 휴가 기록들(leave_date, hours).
    - 입사 1년 미만: 매달 1일씩 발생한 만큼만(입사 후 누적 사용분과 비교)
    - 입사 1년 이상: 올해(1/1~12/31) 연 annual_days일을 자유롭게(올해 사용분과 비교)
    사용 = 오늘까지 쓴 것, 예약 = 앞으로 쓸 것. 남은 = 부여 − 사용 − 예약."""
    months = _full_months(hire, today)
    probation = months < 12
    if probation:
        granted = min(float(annual_days), float(months)) * 8
        scope = [x for x in leaves if _leave_d(x["leave_date"]) >= hire]
    else:
        granted = float(annual_days) * 8
        scope = [x for x in leaves if _leave_d(x["leave_date"]).year == today.year]
    used = sum(float(x["hours"]) for x in scope if _leave_d(x["leave_date"]) <= today)
    booked = sum(float(x["hours"]) for x in scope if _leave_d(x["leave_date"]) > today)
    return {
        "probation": probation, "months": months, "granted_h": granted, "used_h": used, "booked_h": booked,
        "remaining_h": granted - used - booked,
        "next_accrual": _add_months(hire, months + 1) if probation and months < 12 else None,
    }


def _today_kst():
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()  # 서버는 UTC라서 한국 날짜로 맞춘다


def _leave_label(x):
    return f"{x['kind']}" + (f" {x['slot']}" if x.get("slot") else "")


# ── 이 화면 전용 도우미 ────────────────────────────────────────
def _active(leaves):
    return [x for x in leaves if not x.get("canceled_at")]


def _in_scope(hire, today, d):
    """이 날짜의 휴가가 지금 잔여 계산에 들어가는지(입사 1년 미만은 입사 후 전체, 1년 이상은 올해만)."""
    return d >= hire if _full_months(hire, today) < 12 else d.year == today.year


def _jsonable(v):
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def _prof_snap(p):
    """프로필을 비교·표시용 값으로 정리. 없으면 None."""
    if not p:
        return None
    return {"hire_date": _leave_d(p["hire_date"]).isoformat(), "annual_days": float(p["annual_days"])}


def _req_snap(x):
    """휴가 기록의 '고칠 수 있는 값'만 뽑아 비교용으로 정리(저장 직전에 그 사이 바뀌었는지 확인할 때 씀)."""
    return {"leave_date": _leave_d(x["leave_date"]).isoformat(), "kind": x["kind"], "slot": x.get("slot") or None,
            "hours": float(x["hours"]), "memo": x.get("memo") or None, "canceled": bool(x.get("canceled_at"))}


def _bal(prof, leaves, today):
    """프로필(_prof_snap 형태)과 휴가 기록으로 잔여 계산."""
    return _leave_balance(_leave_d(prof["hire_date"]), float(prof["annual_days"]), _active(leaves), today)


def _sim_leaves(leaves, op):
    """이 변경을 저장했다고 치고 휴가 기록 목록을 다시 만든다(실제 DB는 건드리지 않음)."""
    out = []
    for x in leaves:
        if op.get("id") and x["id"] == op["id"]:
            if op["type"] == "delete":
                continue
            if op["type"] == "cancel":
                x = {**x, "canceled_at": "pending"}
            elif op["type"] == "update":
                x = {**x, **op["after"]}
        out.append(x)
    if op["type"] == "add":
        out.append({"id": "__new__", **op["after"], "canceled_at": None})
    return out


def _check_request(hire, leaves, rid, d, kind, slot, today):
    """고치거나 추가하려는 휴가를 확인. (막아야 하는 문제, 경고만 할 문제)를 돌려준다.
    대표 권한이라 주말·입사 전·잔여 부족은 경고만 하고 막지 않는다. 겹치는 시간대는 이중 차감이라 막는다."""
    errors, warns = [], []
    if kind != "연차" and slot not in LEAVE_SLOTS.get(kind, []):
        errors.append("시간대를 골라주세요.")
    taken = set()
    for x in _active(leaves):
        if x["id"] != rid and _leave_d(x["leave_date"]) == d:
            taken |= _leave_quarters(x["kind"], x.get("slot"))
    if taken & _leave_quarters(kind, slot):
        errors.append(f"{d.isoformat()}에는 이미 겹치는 휴가가 있어요. 시간대를 바꾸거나 기존 기록을 먼저 고쳐주세요.")
    if d.weekday() >= 5:
        warns.append(f"{d.isoformat()}은(는) 주말이에요.")
    if d < hire:
        warns.append(f"입사일({hire.isoformat()}) 이전 날짜예요.")
    elif not _in_scope(hire, today, d):
        warns.append("이 날짜는 지금 잔여 계산 범위(입사 1년 미만=입사 후 전체, 1년 이상=올해) 밖이라 잔여 휴가에는 영향이 없어요.")
    return errors, warns


def _bal_text(h):
    return f"{_fmt_days(h)}일 ({h:g}시간)"


# ── 데이터 읽기 (직원앱처럼 캐시 로더 + 저장 후 .clear()) ─────────
@st.cache_data(ttl=30)
def load_people():
    profs = SUPA.table("leave_profiles").select("person").execute().data
    reqs = SUPA.table("leave_requests").select("person").execute().data
    return sorted({r["person"] for r in profs} | {r["person"] for r in reqs})


@st.cache_data(ttl=30)
def load_person(person):
    prof = SUPA.table("leave_profiles").select("*").eq("person", person).execute().data
    leaves = SUPA.table("leave_requests").select("*").eq("person", person).order("leave_date").order("created_at").execute().data
    return (prof[0] if prof else None), leaves


@st.cache_data(ttl=30)
def load_audit(person):
    return SUPA.table("leave_audit_log").select("*").eq("person", person).order("created_at", desc=True).limit(30).execute().data


def _clear_caches():
    load_people.clear(); load_person.clear(); load_audit.clear()


# ── 상태 관리 ──────────────────────────────────────────────────
def _stage(op):
    """'변경 내용 확인'/'취소'/'삭제' 버튼: 아직 저장하지 않고 확인표만 띄운다."""
    st.session_state["la_pending"] = op
    st.session_state["la_edit"] = None


def _open_edit(rid):
    st.session_state["la_edit"] = rid
    st.session_state["la_pending"] = None


def _close_edit():
    st.session_state["la_edit"] = None


def _discard_pending():
    st.session_state["la_pending"] = None


def _reset_work():
    """직원을 바꾸면 작업 중이던 것을 모두 비운다."""
    st.session_state["la_pending"] = None
    st.session_state["la_edit"] = None


# ── 저장 (확인 버튼을 눌렀을 때만 실행) ────────────────────────
ACTION_BY_TYPE = {"profile": "profile_update", "add": "request_add", "update": "request_update",
                  "cancel": "request_cancel", "delete": "request_delete"}


def _commit(op):
    """확인된 변경 하나를 DB에 쓴다. (성공 여부, 안내 문구, 이력 경고) 를 돌려준다.
    저장 직전에 DB를 다시 읽어서, 확인표를 띄운 뒤 그 사이 값이 바뀌었으면 저장하지 않고 멈춘다."""
    t, person = op["type"], op["person"]
    now = datetime.now(timezone.utc).isoformat()
    target_id, before_data, after_data = op.get("id"), None, None

    if t == "profile":
        cur = SUPA.table("leave_profiles").select("*").eq("person", person).execute().data
        if _prof_snap(cur[0] if cur else None) != op["before"]:
            return False, "확인표를 띄운 뒤 입사일/부여일수가 다른 곳에서 바뀌었어요. 저장하지 않았어요. 새로고침 후 다시 해주세요.", None
        payload = {"hire_date": op["after"]["hire_date"], "annual_days": op["after"]["annual_days"], "updated_at": now}
        if cur:
            res = SUPA.table("leave_profiles").update(payload).eq("person", person).execute()
        else:
            res = SUPA.table("leave_profiles").insert({"person": person, **payload}).execute()
        before_data, after_data = (cur[0] if cur else None), (res.data[0] if res.data else payload)
        msg = f"{person}님의 입사일·연간 부여일수를 저장했어요."
    elif t == "add":
        payload = {"person": person, **op["after"]}
        res = SUPA.table("leave_requests").insert(payload).execute()
        if not res.data:
            return False, "추가하지 못했어요(DB가 저장 결과를 돌려주지 않았어요). 목록을 확인해주세요.", None
        target_id, after_data = res.data[0]["id"], res.data[0]
        msg = f"{person}님의 휴가를 추가했어요."
    else:
        cur = SUPA.table("leave_requests").select("*").eq("id", target_id).execute().data
        if not cur:
            return False, "이 휴가 기록이 이미 없어요(다른 곳에서 삭제됐을 수 있어요). 저장하지 않았어요.", None
        if _req_snap(cur[0]) != _req_snap(op["before_row"]):
            return False, "확인표를 띄운 뒤 이 휴가 기록이 다른 곳에서 바뀌었어요. 저장하지 않았어요. 목록을 다시 보고 해주세요.", None
        before_data = cur[0]
        if t == "update":
            res = SUPA.table("leave_requests").update(op["after"]).eq("id", target_id).execute()
            msg = "휴가 기록을 고쳤어요."
        elif t == "cancel":
            res = SUPA.table("leave_requests").update({"canceled_at": now}).eq("id", target_id).execute()
            msg = "휴가를 취소 처리했어요. 잔여 휴가에 다시 반영했어요."
        else:
            res = SUPA.table("leave_requests").delete().eq("id", target_id).execute()
            msg = "휴가 기록을 삭제했어요."
        if not res.data:
            return False, "저장되지 않았어요(대상 행을 찾지 못했어요). 목록을 확인해주세요.", None
        after_data = None if t == "delete" else res.data[0]

    warn = None
    try:  # 이력 기록 실패가 이미 끝난 저장을 되돌리지는 않는다. 대신 화면에 이유를 보여준다.
        SUPA.table("leave_audit_log").insert(_jsonable({
            "actor": ADMIN_EMAIL, "person": person, "action": ACTION_BY_TYPE[t], "target_id": target_id,
            "before_data": before_data, "after_data": after_data,
        })).execute()
    except Exception as e:
        warn = (f"변경은 저장됐지만 변경 이력은 남기지 못했어요 ({type(e).__name__}: {e}). "
                "migrations/20261007_leave_audit_log.sql 을 DB에 적용했는지 확인해주세요.")
    return True, msg, warn


# ── 화면 조각 ──────────────────────────────────────────────────
def _req_view(x):
    d = _leave_d(x["leave_date"])
    return {"날짜": f"{d.isoformat()} ({_WEEKDAY_KO[d.weekday()]})", "종류": _leave_label(x),
            "메모": x.get("memo") or "(없음)", "상태": "취소됨" if x.get("canceled_at") else "정상"}


def _balance_rows(b_before, b_after):
    """잔여 휴가 전/후 비교 행. 바뀐 항목만, 잔여는 항상 보여준다."""
    rows = []
    for key, label in (("granted_h", "부여"), ("used_h", "사용(오늘까지)"), ("booked_h", "예약(앞으로)"), ("remaining_h", "잔여 휴가")):
        if key == "remaining_h" or abs(b_before[key] - b_after[key]) > 1e-9:
            rows.append({"항목": f"📊 {label}", "바꾸기 전": _bal_text(b_before[key]), "→ 바꾼 값": _bal_text(b_after[key])})
    return rows


def _diff_rows(op, prof, leaves, today):
    """확인표: 항목별 '바꾸기 전 → 바꾼 값' + 잔여 휴가 변화."""
    rows = []
    if op["type"] == "profile":
        b, a = op["before"], op["after"]
        for key, label, fmt in (("hire_date", "입사일", str), ("annual_days", "연간 부여일수", lambda v: f"{v:g}일")):
            bv = fmt(b[key]) if b else "(없음)"
            av = fmt(a[key])
            if bv != av:
                rows.append({"항목": label, "바꾸기 전": bv, "→ 바꾼 값": av})
        b_bal = _bal(b, leaves, today) if b else None
        a_bal = _bal(a, leaves, today)
        return rows + (_balance_rows(b_bal, a_bal) if b_bal else [{"항목": "📊 잔여 휴가", "바꾸기 전": "(계산 불가)", "→ 바꾼 값": _bal_text(a_bal["remaining_h"])}])

    before_row = op.get("before_row")
    after_row = None
    if op["type"] == "add":
        after_row = {**op["after"], "canceled_at": None}
    elif op["type"] == "update":
        after_row = {**before_row, **op["after"]}
    elif op["type"] == "cancel":
        after_row = {**before_row, "canceled_at": "pending"}
    bv_all = _req_view(before_row) if before_row else None
    av_all = _req_view(after_row) if after_row else None
    for key in ("날짜", "종류", "메모", "상태"):
        bv = bv_all[key] if bv_all else "(없음)"
        av = av_all[key] if av_all else "(삭제됨)"
        if bv_all and av_all and bv == av:
            continue
        rows.append({"항목": key, "바꾸기 전": bv, "→ 바꾼 값": av})
    p = _prof_snap(prof)
    return rows + _balance_rows(_bal(p, leaves, today), _bal(p, _sim_leaves(leaves, op), today))


PENDING_TITLE = {"profile": "입사일·연간 부여일수 수정", "add": "휴가 대신 추가", "update": "휴가 기록 수정",
                 "cancel": "휴가 취소(취소 처리)", "delete": "휴가 기록 삭제"}


def _render_pending(op, prof, leaves, today):
    with st.container(border=True):
        st.markdown(f"#### 🧾 저장 전 확인 — {op['person']} · {PENDING_TITLE[op['type']]}")
        st.warning("⚠️ **아직 저장되지 않았어요.** 아래 '바꾸기 전 → 바꾼 값'을 확인하고 **확인하고 저장**을 눌러야 반영돼요.")
        st.table(_diff_rows(op, prof, leaves, today))
        if op["type"] == "delete":
            st.error("삭제한 기록은 되돌릴 수 없어요. (변경 이력 테이블이 있으면 삭제 전 값이 이력에 남아요)")
        if op["type"] == "cancel":
            st.caption("취소는 기록을 지우지 않고 canceled_at 에 시각만 채워요. 직원앱에서도 '취소됨'으로 처리돼 잔여에 다시 합산돼요.")
        c1, c2 = st.columns([1, 1])
        if c1.button("✅ 확인하고 저장", type="primary", key="la_confirm", use_container_width=True):
            try:
                ok, msg, warn = _commit(op)
            except Exception as e:
                ok, msg, warn = False, f"저장하지 못했어요 ({type(e).__name__}: {e})", None
            if ok:
                st.session_state["la_flash"] = msg
                st.session_state["la_flash_warn"] = warn
                st.session_state["la_pending"] = None
                st.session_state["la_edit"] = None
                st.session_state["la_ver"] = st.session_state.get("la_ver", 0) + 1  # 입력칸을 DB 값으로 다시 채운다
                _clear_caches()
                st.rerun()
            else:
                st.error(msg)
        c2.button("↩️ 저장하지 않고 닫기", key="la_discard", use_container_width=True, on_click=_discard_pending)


def _request_form(prefix, hire, prof_snap, leaves, today, rid=None, base=None, ver=0):
    """휴가 한 건의 날짜·종류·메모 입력칸 + 실시간 검증 + 잔여 미리보기. 확인 가능한 변경이면 (after, ok) 를 돌려준다.
    base 가 있으면 수정(그 기록의 현재 값이 기본값), 없으면 새로 추가."""
    base_d = _leave_d(base["leave_date"]) if base else today
    base_kind = base["kind"] if base else "연차"
    kinds = list(LEAVE_HOURS)
    k = f"{prefix}_{ver}"
    c1, c2, c3 = st.columns([1, 1, 1])
    d = c1.date_input("날짜", value=base_d, min_value=date(2000, 1, 1), max_value=date(today.year + 2, 12, 31), key=f"{k}_date")
    kind = c2.radio("종류", kinds, index=kinds.index(base_kind), horizontal=True, key=f"{k}_kind",
                    format_func=lambda x: f"{x} ({_fmt_days(LEAVE_HOURS[x] * 1.0)}일)")
    slot = None
    if kind != "연차":
        opts = LEAVE_SLOTS[kind]
        idx = opts.index(base["slot"]) if base and base_kind == kind and base.get("slot") in opts else 0
        slot = c3.selectbox("시간대", opts, index=idx, key=f"{k}_slot_{kind}")
    memo = st.text_input("메모 (선택)", value=(base.get("memo") or "") if base else "", key=f"{k}_memo", placeholder="예: 병원 / 가족 행사")

    after = {"leave_date": d.isoformat(), "kind": kind, "slot": slot, "hours": LEAVE_HOURS[kind], "memo": memo.strip() or None}
    errors, warns = _check_request(hire, leaves, rid, d, kind, slot, today)
    changed = True if base is None else _req_snap({**base, **after}) != _req_snap(base)

    # 잔여 휴가 미리보기: 이 변경을 저장하면 오늘 기준 잔여가 어떻게 되는지
    sim_op = {"type": "add", "after": after} if base is None else {"type": "update", "id": rid, "after": after}
    b_before, b_after = _bal(prof_snap, leaves, today), _bal(prof_snap, _sim_leaves(leaves, sim_op), today)
    if changed:
        st.markdown(f"📊 **잔여 휴가(오늘 기준)**: {_bal_text(b_before['remaining_h'])} → **{_bal_text(b_after['remaining_h'])}**")
        if b_after["remaining_h"] < -1e-9:
            warns.append("저장하면 잔여 휴가가 마이너스가 돼요. (대표 권한이라 저장은 할 수 있어요)")
    else:
        st.caption("아직 바뀐 내용이 없어요.")
    for e in errors:
        st.error(e)
    for w in warns:
        st.warning(w)
    return after, (changed and not errors)


# ── 본문 ───────────────────────────────────────────────────────
today = _today_kst()
ver = st.session_state.get("la_ver", 0)
st.session_state.setdefault("la_pending", None)
st.session_state.setdefault("la_edit", None)

if st.session_state.get("la_flash"):
    st.success(st.session_state.pop("la_flash"))
if st.session_state.get("la_flash_warn"):
    st.warning(st.session_state.pop("la_flash_warn"))

try:
    people = load_people()
except Exception as e:
    st.error(f"❌ 휴가 정보를 불러오지 못했어요 ({type(e).__name__}: {e}). DB에 leave_profiles / leave_requests 테이블이 있는지 확인해주세요.")
    st.stop()
if not people:
    st.info("아직 휴가 정보를 입력한 직원이 없어요.")
    st.stop()

person = st.selectbox("직원", people, key="la_person", on_change=_reset_work)
try:
    prof, leaves = load_person(person)
except Exception as e:
    st.error(f"❌ {person}님의 휴가 정보를 불러오지 못했어요 ({type(e).__name__}: {e})")
    st.stop()
prof_snap = _prof_snap(prof)
st.caption(f"기준일(오늘, 한국 시간): {today.isoformat()} · 1일 = 8시간 · 반차 4시간 · 반반차 2시간 · 입사 1년 미만은 입사일 기준 꽉 채운 달마다 1일씩 발생, 1년 이상은 연간 부여일수(기본 15일)를 그해 자유롭게 사용")

# 현재 잔여 요약
if prof_snap:
    bal = _bal(prof_snap, leaves, today)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("부여", f"{_fmt_days(bal['granted_h'])}일", "입사 1년 미만 · 발생분" if bal["probation"] else f"올해 연 {prof_snap['annual_days']:g}일", delta_color="off")
    m2.metric("사용(오늘까지)", f"{_fmt_days(bal['used_h'])}일")
    m3.metric("예약(앞으로)", f"{_fmt_days(bal['booked_h'])}일")
    m4.metric("✅ 잔여", f"{_fmt_days(bal['remaining_h'])}일", f"{bal['remaining_h']:g}시간", delta_color="off")
    if bal["probation"] and bal["next_accrual"]:
        st.caption(f"입사 {bal['months']}개월차 · 다음 발생일 {bal['next_accrual'].isoformat()} (+1일)")
else:
    st.info(f"{person}님은 아직 입사일을 입력하지 않았어요. 아래에서 입사일을 입력하면 새로 만들어져요. 입사일이 있어야 잔여 휴가를 계산할 수 있어요.")

# 저장 전 확인표
pending = st.session_state.get("la_pending")
if pending and pending["person"] == person:
    _render_pending(pending, prof, leaves, today)

# 1) 입사일·연간 부여일수
with st.expander("👤 입사일 · 연간 부여일수", expanded=prof_snap is None):
    hire_default = _leave_d(prof["hire_date"]) if prof else None
    pc1, pc2 = st.columns(2)
    new_hire = pc1.date_input("입사일", value=hire_default, min_value=date(2000, 1, 1), max_value=max(today, hire_default or today), key=f"la_hire_{person}_{ver}")
    new_annual = pc2.number_input("연간 부여일수", min_value=0.0, step=0.5, format="%g",
                                  value=float(prof["annual_days"]) if prof else 15.0, key=f"la_annual_{person}_{ver}")
    if new_hire is None:
        st.caption("입사일을 입력해주세요.")
    else:
        after_p = {"hire_date": new_hire.isoformat(), "annual_days": float(new_annual)}
        if after_p == prof_snap:
            st.caption("아직 바뀐 내용이 없어요.")
        else:
            a_bal = _bal(after_p, leaves, today)
            if prof_snap:
                st.markdown(f"📊 **잔여 휴가(오늘 기준)**: {_bal_text(bal['remaining_h'])} → **{_bal_text(a_bal['remaining_h'])}** "
                            f"(부여 {_bal_text(bal['granted_h'])} → {_bal_text(a_bal['granted_h'])})")
            else:
                st.markdown(f"📊 **잔여 휴가(오늘 기준)**: **{_bal_text(a_bal['remaining_h'])}**")
            if a_bal["remaining_h"] < -1e-9:
                st.warning("이렇게 저장하면 잔여 휴가가 마이너스가 돼요.")
            st.button("변경 내용 확인", key=f"la_prof_stage_{ver}", on_click=_stage,
                      args=({"type": "profile", "person": person, "before": prof_snap, "after": after_p},))

if prof_snap:
    hire = _leave_d(prof_snap["hire_date"])

    # 2) 휴가 기록 (날짜순)
    st.markdown("### 📋 휴가 기록 (날짜순)")
    show_canceled = st.checkbox("취소된 기록도 보기", value=True, key="la_show_canceled")
    shown = sorted([x for x in leaves if show_canceled or not x.get("canceled_at")],
                   key=lambda x: (str(x["leave_date"]), str(x.get("created_at") or "")))
    if not shown:
        st.caption("휴가 기록이 없어요.")
    editing = st.session_state.get("la_edit")
    for x in shown:
        d = _leave_d(x["leave_date"])
        canceled = bool(x.get("canceled_at"))
        c1, c2, c3, c4 = st.columns([6, 1, 1, 1])
        line = f"**{d.isoformat()} ({_WEEKDAY_KO[d.weekday()]})** · {_leave_label(x)} · {_fmt_days(float(x['hours']))}일" + (f" · {x['memo']}" if x.get("memo") else "")
        c1.markdown(f"~~{line}~~ 🚫 취소됨({str(x['canceled_at'])[:10]})" if canceled else line)
        if not canceled:
            c2.button("✏️ 수정", key=f"la_edit_btn_{x['id']}", use_container_width=True, on_click=_open_edit, args=(x["id"],))
            c3.button("🚫 취소", key=f"la_cancel_btn_{x['id']}", use_container_width=True, on_click=_stage,
                      args=({"type": "cancel", "person": person, "id": x["id"], "before_row": x},))
        c4.button("🗑️ 삭제", key=f"la_del_btn_{x['id']}", use_container_width=True, on_click=_stage,
                  args=({"type": "delete", "person": person, "id": x["id"], "before_row": x},))
        if editing == x["id"] and not canceled:
            with st.container(border=True):
                after, ok = _request_form(f"la_ed_{x['id']}", hire, prof_snap, leaves, today, rid=x["id"], base=x, ver=ver)
                b1, b2 = st.columns([1, 1])
                b1.button("변경 내용 확인", key=f"la_ed_stage_{x['id']}_{ver}", type="primary", disabled=not ok, use_container_width=True,
                          on_click=_stage, args=({"type": "update", "person": person, "id": x["id"], "before_row": x, "after": after},))
                b2.button("닫기", key=f"la_ed_close_{x['id']}", use_container_width=True, on_click=_close_edit)

    # 3) 대신 추가
    st.markdown("### ➕ 휴가 대신 추가")
    with st.container(border=True):
        st.caption("직원 대신 휴가를 등록해요. 취소·삭제와 마찬가지로 저장 전에 확인표가 먼저 떠요.")
        after, ok = _request_form("la_add", hire, prof_snap, leaves, today, ver=ver)
        st.button("추가 내용 확인", key=f"la_add_stage_{ver}", type="primary", disabled=not ok,
                  on_click=_stage, args=({"type": "add", "person": person, "after": after},))

# 변경 이력
with st.expander(f"📜 {person}님의 변경 이력 (최근 30건)"):
    try:
        log = load_audit(person)
    except Exception as e:
        log = None
        st.caption(f"변경 이력 테이블을 읽지 못했어요 ({type(e).__name__}). migrations/20261007_leave_audit_log.sql 을 DB에 적용하면 여기에 쌓여요.")
    if log is not None:
        if not log:
            st.caption("아직 남은 이력이 없어요.")
        else:
            ACTION_KO = {"profile_update": "입사일·부여일수 수정", "request_update": "휴가 수정", "request_cancel": "휴가 취소",
                         "request_delete": "휴가 삭제", "request_add": "휴가 대신 추가"}

            def _brief(v):
                if not v:
                    return "-"
                if "kind" in v:
                    return f"{str(v.get('leave_date'))[:10]} {_leave_label(v)}" + (f" · {v['memo']}" if v.get("memo") else "") + (" · 취소됨" if v.get("canceled_at") else "")
                return f"입사일 {str(v.get('hire_date'))[:10]} · 연 {float(v.get('annual_days', 0)):g}일"

            st.table([{"시각(KST)": (datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00")) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
                       "작업": ACTION_KO.get(r["action"], r["action"]), "바꾸기 전": _brief(r.get("before_data")), "→ 바꾼 값": _brief(r.get("after_data"))}
                      for r in log])
