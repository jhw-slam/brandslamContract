import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 레포 루트의 ceo_common.py 를 어디서 실행해도 찾게
from ceo_common import CHECK_EVERY_DAYS, STATUS_LABEL, build_receivables, summarize, to_date

st.set_page_config(page_title="Receivables", layout="wide")

# ── 앱 공통 비밀번호 게이트 (다른 페이지와 동일. 대표 전용 비밀번호는 따로 걸지 않음) ──
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

TODAY = (datetime.now(timezone(timedelta(hours=9)))).date()
RED, GREEN, GRAY = "#d9442b", "#1f9d62", "#8a8f98"


def won(n):
    n = float(n or 0)
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f}억"
    if abs(n) >= 1e4:
        return f"{n / 1e4:,.0f}만원"
    return f"{n:,.0f}원"


def tryq(fn, default):
    try:
        return fn(), None
    except Exception as e:
        return default, f"{type(e).__name__}: {str(e)[:140]}"


# ── 데이터 읽기 (30초 캐시, 저장하면 clear_all) ─────────────────
@st.cache_data(ttl=30)
def load_receivable_data():
    ev, e1 = tryq(lambda: SUPA.table("cash_events").select("id,project_id,title,amount,due_date,paid").eq("direction", "in")
                  .eq("paid", False).execute().data, [])
    projs, _ = tryq(lambda: SUPA.table("projects").select("id,brand,campaign").execute().data, [])
    matched, _ = tryq(lambda: SUPA.table("bank_transactions").select("matched_cash_event_id").eq("direction", "in").execute().data, [])
    checks, e2 = tryq(lambda: SUPA.table("ceo_receivable_checks").select("id,cash_event_id,status,received_amount,note,checked_at")
                      .order("checked_at", desc=True).execute().data, [])
    return {"ev": ev, "projs": {p["id"]: p for p in projs}, "matched": {r["matched_cash_event_id"] for r in matched if r.get("matched_cash_event_id")},
            "checks": checks, "missing_checks": bool(e2), "err": e1 or e2}


@st.cache_data(ttl=30)
def load_deposits(since_iso):
    rows, e1 = tryq(lambda: SUPA.table("bank_transactions")
                    .select("id,txn_date,amount,description,account_label,account_category_id,matched_cash_event_id,source")
                    .eq("direction", "in").gte("txn_date", since_iso).order("txn_date", desc=True).limit(1000).execute().data, [])
    cats, _ = tryq(lambda: SUPA.table("fin_account_categories").select("id,name,type").execute().data, [])
    ids = [r["matched_cash_event_id"] for r in rows if r.get("matched_cash_event_id")]
    evs, _ = tryq(lambda: SUPA.table("cash_events").select("id,title,project_id").in_("id", ids).execute().data, []) if ids else ([], None)
    projs, _ = tryq(lambda: SUPA.table("projects").select("id,brand").execute().data, [])
    return {"rows": rows, "cats": {c["id"]: c for c in cats}, "evs": {e["id"]: e for e in evs}, "projs": {p["id"]: p for p in projs}, "err": e1}


def clear_all():
    load_receivable_data.clear(); load_deposits.clear()


def flash(msg, ok=True):
    st.session_state["rc_flash"] = (msg, ok)


# ── 저장 (버튼을 눌렀을 때만) ─────────────────────────────────
def record_check(event_id, status, amount_key=None):
    payload = {"cash_event_id": event_id, "status": status}
    if status == "partial":
        amt = float(st.session_state.get(amount_key) or 0)
        if amt <= 0:
            flash("부분입금은 받은 금액을 1원 이상 입력해주세요.", False)
            return
        payload["received_amount"] = amt
    try:
        SUPA.table("ceo_receivable_checks").insert(payload).execute()
        clear_all()
        flash({"received": "전액 받음으로 표시했어요. 받을 돈에서 뺐어요.",
               "partial": "부분입금을 기록했어요. 남은 금액만 받을 돈에 남아요.",
               "pending": f"아직 못 받음으로 표시했어요. {CHECK_EVERY_DAYS}일 뒤 다시 '체크 필요'로 알려드려요.",
               "canceled": "계약취소로 표시했어요. 받을 돈에서 뺐어요."}[status])
    except Exception as e:
        flash(f"저장하지 못했어요 ({type(e).__name__}: {str(e)[:160]}). migrations/20261009_ceo_receivable_partial_cancel.sql 이 DB에 적용됐는지 확인해주세요.", False)


def undo_check(event_id):
    try:
        SUPA.table("ceo_receivable_checks").insert({"cash_event_id": event_id, "status": "pending"}).execute()
        clear_all()
        flash("되돌렸어요. 다시 받을 돈에 나타나요.")
    except Exception as e:
        flash(f"되돌리지 못했어요 ({type(e).__name__}: {str(e)[:160]})", False)


# ══════════════════════════════════════════════════════════════
st.title("💰 Receivables")
st.caption("받을 돈을 확인·처리하고, 은행으로 들어온 돈을 한곳에서 봅니다. (Command Center는 여기 내용을 현황으로만 보여줘요)")

if st.session_state.get("rc_flash"):
    _m, _ok = st.session_state.pop("rc_flash")
    (st.success if _ok else st.error)(_m)

data = load_receivable_data()
rc = build_receivables(data["ev"], data["projs"], data["matched"], data["checks"], TODAY)
sm = summarize(rc["open"])

if data["missing_checks"]:
    st.info("📌 ceo_receivable_checks 테이블을 읽지 못해서 확인 버튼이 꺼져 있어요. `migrations/20261009_ceo_command_center.sql` 과 "
            "`20261009_ceo_receivable_partial_cancel.sql` 을 DB에 적용하면 켜져요.")

m1, m2, m3, m4 = st.columns(4)
m1.metric("받을 돈(남은 금액)", won(sm["total"]) if sm["total"] else "0원", f"{sm['n']}건", delta_color="off")
m2.metric("🔴 체크 필요", f"{sm['need_n']}건", won(sm["need_sum"]) if sm["need_sum"] else "0원", delta_color="off")
m3.metric("기한 경과", f"{sm['late_n']}건", f"최대 {sm['max_over']}일" if sm["late_n"] else "없음", delta_color="off")
m4.metric("부분입금으로 받은 금액", won(sm["partial_got"]) if sm["partial_got"] else "0원")

tab_rc, tab_dep = st.tabs(["받을 돈 확인", "최근 입금내역 (미매칭 포함)"])

# ── 받을 돈 확인 ──────────────────────────────────────────────
with tab_rc:
    st.caption("남은 금액이 큰 순서예요. 은행 매칭 없이도 대표님이 확인하면 숫자가 정확해져요. 이 확인은 재무 데이터(cash_events)를 바꾸지 않고 따로 기록돼요.")
    if not rc["open"]:
        st.info("받을 돈(예정) 건이 없어요 — 0건")
    for r in rc["open"]:
        with st.container(border=True):
            left, right = st.columns([5, 4])
            ck = r["check"]
            due = r["due_date"]
            due_txt = (f":red[**{r['overdue_days']}일 경과**] (기한 {due})" if r["overdue_days"] else (f"기한 {due} (D-{(due - TODAY).days})" if due else "기한 미정"))
            money = f"예정 **{won(r['amount'])}**"
            if r["received"]:
                money += f" · 받은 **{won(r['received'])}** · 남은 **{won(r['remaining'])}**"
            last = ("🔴 **체크 필요**" if r["need_check"] else f"✔ 마지막 확인 {STATUS_LABEL[ck['status']]}") if ck or r["need_check"] else ""
            left.markdown(f"**{r['brand'] or '(프로젝트 없음)'}** · {r['title']}\n\n{money}  \n{due_txt}  \n{last}")
            b1, b2, b3, b4 = right.columns(4)
            dis = data["missing_checks"]
            b1.button("✅ 받음", key=f"rc_full_{r['id']}", on_click=record_check, args=(r["id"], "received"), disabled=dis, use_container_width=True,
                      help="전액 받았어요")
            with b2.popover("💵 부분", disabled=dis, use_container_width=True):
                akey = f"rc_amt_{r['id']}"
                st.number_input("지금까지 받은 금액(누적, 원)", min_value=0.0, step=1_000_000.0, format="%.0f", value=float(r["received"]), key=akey)
                st.caption(f"예정 {won(r['amount'])} 이상 입력하면 전액 받음으로 처리돼요.")
                st.button("부분입금 저장", key=f"rc_part_{r['id']}", on_click=record_check, args=(r["id"], "partial", akey), type="primary")
            b3.button("⏳ 아직", key=f"rc_wait_{r['id']}", on_click=record_check, args=(r["id"], "pending"), disabled=dis, use_container_width=True,
                      help="아직 못 받았어요")
            with b4.popover("✖ 취소", disabled=dis, use_container_width=True):
                st.warning("계약이 취소돼서 더 받을 돈이 없는 경우예요. 받을 돈에서 빠집니다. (아래 '정리된 건'에서 되돌릴 수 있어요)")
                st.button("계약취소로 표시", key=f"rc_cancel_{r['id']}", on_click=record_check, args=(r["id"], "canceled"), type="primary")

    if rc["closed"]:
        with st.expander(f"정리된 건 {len(rc['closed'])}건 (받음 · 계약취소 · 은행 매칭)"):
            tag = {"received": "✅ 받음", "canceled": "✖ 계약취소", "matched": "🏦 은행 매칭됨"}
            for r in rc["closed"]:
                c1, c2 = st.columns([6, 1])
                c1.markdown(f"{tag.get(r['state'], r['state'])} · **{r['brand'] or '(프로젝트 없음)'}** · {r['title']} · {won(r['amount'])}")
                if r["state"] in ("received", "canceled"):
                    c2.button("되돌리기", key=f"rc_undo_{r['id']}", on_click=undo_check, args=(r["id"],))

# ── 최근 입금내역 ─────────────────────────────────────────────
with tab_dep:
    st.caption("은행(뱅크다)으로 들어온 입금 전체예요. 계약(받을 돈)과 연결되지 않은 **미매칭** 입금도 모두 보여요.")
    f1, f2, f3 = st.columns([2, 2, 2])
    days = f1.radio("기간", [30, 90, 180, 365], index=1, format_func=lambda d: f"최근 {d}일", horizontal=True, key="dep_days")
    view = f2.radio("보기", ["전체", "미매칭만", "매칭됨만"], horizontal=True, key="dep_view")
    hide_transfer = f3.checkbox("내부이체(계좌간 이동) 제외", value=True, key="dep_hide_tr")
    dep = load_deposits((TODAY - timedelta(days=days)).isoformat())
    if dep["err"]:
        st.error(f"입금내역을 읽지 못했어요: {dep['err']}")
    rows = []
    for r in dep["rows"]:
        cat = dep["cats"].get(r.get("account_category_id"))
        if hide_transfer and cat and cat.get("type") == "transfer":
            continue
        matched = bool(r.get("matched_cash_event_id"))
        ev = dep["evs"].get(r.get("matched_cash_event_id")) or {}
        brand = (dep["projs"].get(ev.get("project_id")) or {}).get("brand") or ""
        rows.append({"입금일": str(r["txn_date"])[:10], "입금자/적요": r.get("description") or "-", "금액": float(r.get("amount") or 0),
                     "계좌": r.get("account_label") or "", "계정과목": cat["name"] if cat else "분류 전",
                     "매칭": f"✅ {brand} {ev.get('title') or ''}".strip() if matched else "⚠️ 미매칭", "_m": matched})
    shown = [x for x in rows if view == "전체" or (view == "미매칭만" and not x["_m"]) or (view == "매칭됨만" and x["_m"])]
    un = [x for x in rows if not x["_m"]]
    d1, d2, d3 = st.columns(3)
    d1.metric("조회 입금", f"{len(rows)}건", won(sum(x["금액"] for x in rows)) if rows else "0원", delta_color="off")
    d2.metric("⚠️ 미매칭", f"{len(un)}건", won(sum(x["금액"] for x in un)) if un else "0원", delta_color="off")
    d3.metric("✅ 매칭됨", f"{len(rows) - len(un)}건")
    if not shown:
        st.info("조건에 맞는 입금이 없어요 — 0건")
    else:
        df = pd.DataFrame(shown).drop(columns=["_m"])
        st.dataframe(df.assign(금액=df["금액"].map(lambda v: f"₩{v:,.0f}")), hide_index=True, use_container_width=True)
        st.download_button("📥 CSV로 받기", df.to_csv(index=False).encode("utf-8-sig"), file_name="최근입금내역.csv", mime="text/csv", key="dep_csv")
    st.caption("매칭은 재무캘린더의 '전체 매칭 현황'에서 하고, 매칭되면 여기 표시가 ✅로 바뀌어요. 분류 전 입금은 재무캘린더에서 계정과목을 정하면 돼요.")
