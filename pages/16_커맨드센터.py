import os
import sys
from pathlib import Path
import html
from datetime import date, datetime, timedelta, timezone

import streamlit as st
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 레포 루트의 ceo_common.py 를 어디서 실행해도 찾게
from ceo_common import STATUS_LABEL, build_receivables, summarize

st.set_page_config(page_title="Command Center", layout="wide")

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

# ══════════════════════════════════════════════════════════════
# 원칙: 데이터가 들어오면 보여준다. 값이 없으면 0 또는 '입력 대기'로 표시하고, 가짜 숫자는 만들지 않는다.
# 설계 문서: docs/ceo_command_center_design.md   DB: migrations/20261009_ceo_command_center*.sql
# ══════════════════════════════════════════════════════════════
RED, AMBER, GREEN, BLUE, GRAY = "#d9442b", "#e8a317", "#1f9d62", "#2a5bd7", "#8a8f98"
CHECK_EVERY_DAYS = 14      # 받을 돈을 대표님이 마지막으로 확인한 뒤 이 일수가 지나면 다시 '체크 필요'
STALE_DAYS = 14            # OKR 항목에 체크인이 이 일수 넘게 없으면 '느림'
STAFF = ["장현우", "김선재", "곽재선", "구정회", "이단우"]
BRAND_DONE_STATES = {"done"}

STAGES = [
    ("S1", "신규고객창출", "New Customers", "새 브랜드·고객을 찾는다"),
    ("S2", "고객영업", "Sales", "계약과 매출을 만든다"),
    ("S3", "서비스제공", "Delivery", "캠페인을 실행하고 계속 개선한다"),
    ("S4", "만족도체크", "Satisfaction", "만족도 확인 · 공짜요소/시즌성 부여"),
    ("S5", "자동화", "Automation", "사람 손을 줄여 리소스를 만든다"),
    ("S6", "재무관리", "Finance", "계정과목별로 매출↑ 비용↓"),
    ("S7", "개선점 조직화", "Learning", "배운 것을 팀의 규칙으로 남긴다"),
    ("S8", "IR 기획", "IR", "다시 영업하기 위한 자료를 만든다"),
]
STAGE_NAME = {c: n for c, n, _, _ in STAGES}

# OKR 항목 → SCM 단계 기본 배치 규칙(위에서부터 먼저 맞는 것). 대표님이 고친 배치(ceo_scm_stage_map)가 항상 우선.
KEYWORD_RULES = [
    ("S1", ["신규고객"]),
    ("S4", ["고객만족", "만족도"]),
    ("S2", ["재계약", "매출", "브랜드확대", "상품개발", "영업", "수금"]),
    ("S5", ["시스템화", "개발 미션", "자동화", "마진율", "브랜드사관리"]),
    ("S7", ["운영루틴", "업무이관", "피드백", "지표", "성과기록", "템플릿"]),
    ("S6", ["재무", "정산"]),
    ("S8", ["ir", "투자"]),
    ("S3", ["인플루언서", "가이드", "콘텐츠", "왕홍", "브랜드", "캠페인", "리서치", "협업", "알바"]),
]
PERSON_FALLBACK = {"김선재": "S2", "곽재선": "S3", "이단우": "S3", "구정회": "S5"}

KST = timezone(timedelta(hours=9))


def today_kst():
    return datetime.now(KST).date()


TODAY = today_kst()


# ── 공통 도우미 ────────────────────────────────────────────────
def esc(v):
    return html.escape(str(v if v is not None else ""))


def won(n):
    """금액: 1억 이상은 '1.12억', 그 아래는 '5,826만원'."""
    n = float(n or 0)
    if abs(n) >= 1e8:
        return f"{n / 1e8:.2f}억"
    if abs(n) >= 1e4:
        return f"{n / 1e4:,.0f}만원"
    return f"{n:,.0f}원"


def to_date(v):
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return date.fromisoformat(str(v)[:10])


def days_ago(ts):
    if not ts:
        return None
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days


def monday_of(d):
    return d - timedelta(days=d.weekday())


def quarter_info(d):
    q = (d.month - 1) // 3 + 1
    end_month = q * 3
    nxt = date(d.year + (end_month == 12), end_month % 12 + 1, 1)
    return f"{d.year}Q{q}", f"{d.year} Q{q}", nxt - timedelta(days=1), date(d.year, end_month - 2, 1)


def tryq(fn, default):
    """DB 읽기. 테이블이 아직 없어도(마이그레이션 전) 화면이 죽지 않게 (값, 오류문구)를 돌려준다."""
    try:
        return fn(), None
    except Exception as e:
        return default, f"{type(e).__name__}: {str(e)[:140]}"


def pill(text, color):
    return f"<span class='cc-pill' style='background:{color}'>{esc(text)}</span>"


# ── CSS (색은 빨강/주황/초록/파랑 + 회색만, 큰 글씨) ──────────────
st.markdown(f"""
<style>
.cc-eyebrow {{ font-size: 15px; letter-spacing: .06em; opacity: .65; font-weight: 600; }}
.cc-title {{ font-size: clamp(34px, 5vw, 48px); font-weight: 800; line-height: 1.15; margin: 2px 0 0; }}
.cc-dday {{ font-size: clamp(40px, 6vw, 64px); font-weight: 800; line-height: 1; text-align: right; }}
.cc-banner {{ background: #1c2b4f; color: #fff; border-radius: 22px; padding: 28px 34px; margin: 14px 0 22px; }}
.cc-banner .k {{ color: #9db4ff; font-size: 15px; font-weight: 700; letter-spacing: .05em; }}
.cc-banner .h {{ font-size: clamp(30px, 4.6vw, 46px); font-weight: 800; line-height: 1.2; margin: 6px 0; }}
.cc-banner .s {{ font-size: 18px; opacity: .85; }}
.cc-card {{ border: 1px solid rgba(128,128,128,.28); border-radius: 20px; padding: 24px 28px; margin-bottom: 16px; }}
.cc-card.priority {{ border: 2px solid {BLUE}; }}
.cc-h2 {{ font-size: clamp(26px, 3.4vw, 34px); font-weight: 800; margin: 0 0 4px; }}
.cc-sub {{ font-size: 16px; opacity: .7; }}
.cc-big {{ font-size: clamp(64px, 9vw, 96px); font-weight: 800; line-height: 1; letter-spacing: -.02em; }}
.cc-big small {{ font-size: clamp(20px, 2.6vw, 28px); font-weight: 700; opacity: .7; margin-left: 6px; }}
.cc-mid {{ font-size: clamp(34px, 4.4vw, 46px); font-weight: 800; line-height: 1.05; }}
.cc-pill {{ display: inline-block; color: #fff; font-weight: 700; font-size: 14px; padding: 3px 12px; border-radius: 99px; margin-right: 6px; }}
.cc-gray {{ color: {GRAY}; }}
.cc-row {{ display: flex; justify-content: space-between; gap: 12px; padding: 10px 0; border-top: 1px solid rgba(128,128,128,.2); font-size: 17px; }}
.cc-row .n {{ font-weight: 700; }}
.cc-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 14px; }}
.cc-stage {{ border: 1px solid rgba(128,128,128,.28); border-left: 8px solid {GRAY}; border-radius: 16px; padding: 16px 18px; position: relative; }}
.cc-stage .no {{ font-size: 13px; font-weight: 800; opacity: .6; letter-spacing: .06em; }}
.cc-stage .nm {{ font-size: 22px; font-weight: 800; margin: 2px 0; }}
.cc-stage .en {{ font-size: 13px; opacity: .6; margin-left: 6px; font-weight: 600; }}
.cc-stage .hl {{ font-size: 40px; font-weight: 800; line-height: 1.1; margin-top: 8px; }}
.cc-stage .hll {{ font-size: 14px; opacity: .7; margin-bottom: 8px; }}
.cc-stage .ln {{ font-size: 14px; display: flex; justify-content: space-between; gap: 8px; padding: 3px 0; border-top: 1px dashed rgba(128,128,128,.25); }}
.cc-stage .own {{ font-size: 13px; margin: 6px 0; }}
.cc-chip {{ display: inline-block; border: 1px solid rgba(128,128,128,.4); border-radius: 99px; padding: 1px 10px; margin: 0 4px 4px 0; font-size: 13px; }}
.cc-dots span {{ display: inline-block; width: 26px; height: 26px; border-radius: 50%; margin-right: 8px; background: rgba(128,128,128,.25); }}
.cc-dots span.on {{ background: {GREEN}; }}
.cc-bar {{ height: 18px; border-radius: 99px; background: rgba(128,128,128,.22); overflow: hidden; margin: 8px 0; }}
.cc-bar i {{ display: block; height: 100%; background: {GREEN}; border-radius: 99px; }}
.cc-trend {{ display: flex; align-items: flex-end; gap: 10px; height: 90px; margin-top: 8px; }}
.cc-trend div {{ flex: 1; max-width: 96px; text-align: center; font-size: 12px; }}
.cc-trend b {{ display: block; background: {BLUE}; border-radius: 6px 6px 0 0; min-height: 3px; }}
.cc-donut {{ width: 110px; height: 110px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 26px; font-weight: 800; }}
.cc-donut span {{ background: var(--background-color, #fff); width: 76px; height: 76px; border-radius: 50%; display: flex; align-items: center; justify-content: center; }}
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
# 데이터 읽기 (60초 캐시. 저장 뒤에는 clear_all())
# ══════════════════════════════════════════════════════════════
def _sel(table, cols="*", **kw):
    return SUPA.table(table).select(cols, **kw)


@st.cache_data(ttl=60)
def load_okr():
    items, e1 = tryq(lambda: _sel("okr_items", "id,person,category,title,target_qty,progress,cadence,due_date,once_date,"
                                  "confirmed,is_recurring,last_checkin_at,achieved_at").execute().data, [])
    org, e2 = tryq(lambda: _sel("okr_org", "person,tag,objective,pending").execute().data, [])
    logs, e3 = tryq(lambda: _sel("okr_period_log", "person,category,title,achieved").execute().data, [])
    smap, e4 = tryq(lambda: _sel("ceo_scm_stage_map", "match_kind,match_value,stage_code").execute().data, [])
    return {"items": items, "org": org, "logs": logs, "smap": smap, "missing_map": bool(e4), "errs": [e for e in (e1, e2, e3) if e]}


@st.cache_data(ttl=60)
def load_money(month_start_iso, since6_iso, since90_iso):
    ev, e1 = tryq(lambda: _sel("cash_events", "id,project_id,title,amount,due_date,paid").eq("direction", "in")
                  .eq("paid", False).execute().data, [])
    projs, _ = tryq(lambda: _sel("projects", "id,brand,campaign").execute().data, [])
    matched, _ = tryq(lambda: _sel("bank_transactions", "matched_cash_event_id").eq("direction", "in")
                      .execute().data, [])
    matched_ids = {r["matched_cash_event_id"] for r in matched if r.get("matched_cash_event_id")}
    checks, e_chk = tryq(lambda: _sel("ceo_receivable_checks", "id,cash_event_id,status,received_amount,note,checked_at")
                         .order("checked_at", desc=True).execute().data, [])
    cats, _ = tryq(lambda: _sel("fin_account_categories", "id,name,type").execute().data, [])
    rev_ids = [c["id"] for c in cats if c.get("type") == "revenue"]
    rev_rows = []
    if rev_ids:
        rev_rows, _ = tryq(lambda: _sel("bank_transactions", "amount,txn_date,account_category_id").eq("direction", "in")
                           .in_("account_category_id", rev_ids).gte("txn_date", since6_iso).limit(1000).execute().data, [])
    tot90, _ = tryq(lambda: _sel("bank_transactions", "id", count="exact").gte("txn_date", since90_iso).limit(1).execute().count, 0)
    unc90, _ = tryq(lambda: _sel("bank_transactions", "id", count="exact").gte("txn_date", since90_iso)
                    .is_("account_category_id", "null").limit(1).execute().count, 0)
    fc, _ = tryq(lambda: _sel("fin_cash_forecasts", "direction,amount,expected_date,status").eq("direction", "in")
                 .eq("status", "open").execute().data, [])
    return {"ev": ev, "projs": {p["id"]: p for p in projs}, "matched_ids": matched_ids, "checks": checks,
            "missing_checks": bool(e_chk), "rev_rows": rev_rows, "tot90": tot90 or 0, "unc90": unc90 or 0,
            "forecasts": fc, "errs": [e for e in (e1,) if e]}


@st.cache_data(ttl=60)
def load_ops(since30_iso, since14_iso):
    acc, _ = tryq(lambda: _sel("sales_accounts", "status,created_at,satisfaction_score,last_contact_date,renewal_date,monthly_budget")
                  .execute().data, [])
    camp, _ = tryq(lambda: _sel("sales_campaigns", "open_date,contract_url,invoice_amount,brand_approved_at").limit(1000).execute().data, [])
    deals, e_deal = tryq(lambda: _sel("sales_pipeline_deals", "brand_name,expected_amount,expected_month,probability,status,submitted_by,note")
                         .execute().data, [])
    plc, _ = tryq(lambda: _sel("influencer_placements", "status,guideline_ok,updated_at").limit(1000).execute().data, [])
    act, _ = tryq(lambda: _sel("daily_activity_log", "manager_feedback,created_at").gte("created_at", since14_iso)
                  .execute().data, [])
    asg, _ = tryq(lambda: _sel("assigned_tasks", "status").execute().data, [])
    meet, _ = tryq(lambda: _sel("meetings", "id", count="exact").gte("created_at", since30_iso).limit(1).execute().count, 0)
    dev, _ = tryq(lambda: _sel("dev_tasks", "status").execute().data, [])
    pool, _ = tryq(lambda: _sel("influencer_pool", "id", count="exact").limit(1).execute().count, 0)
    funnel, _ = tryq(lambda: _sel("casting_funnel", "id", count="exact").limit(1).execute().count, 0)
    vision, _ = tryq(lambda: _sel("company_vision", "updated_at").order("updated_at", desc=True).limit(1).execute().data, [])
    return {"acc": acc, "camp": camp, "deals": deals, "missing_deals": bool(e_deal), "plc": plc, "act": act, "asg": asg,
            "meet": meet or 0, "dev": dev, "pool": pool or 0, "funnel": funnel or 0, "vision": vision}


@st.cache_data(ttl=60)
def load_ceo(week_iso, quarter):
    foc, e1 = tryq(lambda: SUPA.table("ceo_okr_weekly_focus").select("*").eq("week_start", week_iso).execute().data, [])
    objs, e2 = tryq(lambda: SUPA.table("ceo_okr_objectives").select("*").eq("quarter", quarter).order("sort").execute().data, [])
    krs, e3 = tryq(lambda: SUPA.table("ceo_okr_krs").select("*").order("sort").execute().data, [])
    brand, e4 = tryq(lambda: SUPA.table("ceo_brand_launch_tasks").select("*").order("due_date").execute().data, [])
    return {"focus": foc[0] if foc else None, "objs": objs, "krs": krs, "brand": brand,
            "missing": {"weekly_focus": bool(e1), "okr": bool(e2 or e3), "brand": bool(e4)}}


def clear_all():
    for f in (load_okr, load_money, load_ops, load_ceo):
        f.clear()


def flash(msg, ok=True):
    st.session_state["cc_flash"] = (msg, ok)


# ══════════════════════════════════════════════════════════════
# 계산: OKR 항목 → SCM 단계 / 속도(시간체크)
# ══════════════════════════════════════════════════════════════
def stage_of(item, smap):
    """우선순위: 대표님이 고친 항목별 배치 > 분류명 배치 > 키워드 기본 규칙 > 담당자 기본값 > 미지정(None)."""
    for r in smap:
        if r["match_kind"] == "okr_item" and r["match_value"] == item["id"]:
            return r["stage_code"]
    for r in smap:
        if r["match_kind"] == "category" and r["match_value"] == (item.get("category") or ""):
            return r["stage_code"]
    text = f"{item.get('category') or ''} {item.get('title') or ''}".lower()
    for code, kws in KEYWORD_RULES:
        if any(k in text for k in kws):
            return code
    return PERSON_FALLBACK.get(item.get("person"))


def item_state(it):
    """항목 하나의 상태: done(달성) / overdue(마감 지남) / stale(체크인 오래됨) / ok."""
    target, prog = float(it.get("target_qty") or 0), float(it.get("progress") or 0)
    if target > 0 and prog >= target:
        return "done"
    due = to_date(it.get("due_date")) or (to_date(it.get("once_date")) if it.get("cadence") == "once" else None)
    if due and due < TODAY:
        return "overdue"
    age = days_ago(it.get("last_checkin_at"))
    if age is None or age > STALE_DAYS:
        return "stale"
    return "ok"


def stage_speed(items):
    """단계에 배치된 OKR 항목들의 시간체크 요약."""
    active = [i for i in items if item_state(i) != "done"]
    overdue = [i for i in active if item_state(i) == "overdue"]
    stale = [i for i in active if item_state(i) in ("stale", "overdue") and (days_ago(i.get("last_checkin_at")) is None or days_ago(i.get("last_checkin_at")) > STALE_DAYS)]
    ages = [days_ago(i.get("last_checkin_at")) for i in active if i.get("last_checkin_at")]
    return {"n": len(items), "active": len(active), "overdue": len(overdue), "stale": len(stale),
            "unconfirmed": sum(1 for i in items if not i.get("confirmed")),
            "oldest": max(ages) if ages else None, "never": sum(1 for i in active if not i.get("last_checkin_at"))}


def stage_status(sp, has_owner, metric_has_data):
    """신호: empty(비어있음) / blocked(막힘) / slow(느림) / nodata(데이터 대기) / ok(정상)."""
    if not has_owner and sp["n"] == 0:
        return "empty"
    if sp["overdue"] > 0:
        return "blocked"
    if sp["active"] > 0 and sp["stale"] / sp["active"] >= 0.5:
        return "slow"
    if sp["n"] == 0 and not metric_has_data:
        return "nodata"
    return "ok"


STATUS_VIEW = {"empty": ("비어있음", RED), "blocked": ("막힘", RED), "slow": ("느림", AMBER),
               "nodata": ("데이터 대기", GRAY), "ok": ("정상", GREEN)}


# ══════════════════════════════════════════════════════════════
# 저장 동작 (버튼을 눌렀을 때만 실행)
# ══════════════════════════════════════════════════════════════
def _write(label, fn):
    try:
        fn()
        clear_all()
        flash(label)
    except Exception as e:
        flash(f"저장하지 못했어요 ({type(e).__name__}: {str(e)[:160]}). migrations/20261009_ceo_command_center.sql 을 DB에 적용했는지 확인해주세요.", False)


# ══════════════════════════════════════════════════════════════
# 화면
# ══════════════════════════════════════════════════════════════
qkey, qlabel, qend, qstart = quarter_info(TODAY)
week = monday_of(TODAY)
month_start = TODAY.replace(day=1)
since6 = (month_start - timedelta(days=155)).replace(day=1)

okr = load_okr()
money = load_money(month_start.isoformat(), since6.isoformat(), (TODAY - timedelta(days=90)).isoformat())
ops = load_ops((TODAY - timedelta(days=30)).isoformat(), (TODAY - timedelta(days=14)).isoformat())
ceo = load_ceo(week.isoformat(), qkey)

if st.session_state.get("cc_flash"):
    _m, _ok = st.session_state.pop("cc_flash")
    (st.success if _ok else st.error)(_m)

missing = [k for k, v in ceo["missing"].items() if v] + (["receivable_checks"] if money["missing_checks"] else []) \
    + (["scm_stage_map"] if okr["missing_map"] else []) + (["sales_pipeline_deals"] if ops["missing_deals"] else [])
if missing:
    st.info("📌 아직 DB에 만들지 않은 테이블이 있어서 해당 칸은 '입력 대기'로 보여요: " + ", ".join(missing)
            + " — `migrations/20261009_ceo_command_center.sql` 을 Supabase에 적용하면 입력·저장이 켜집니다. (그 전에도 화면은 정상이에요)")

# ── 헤더 ──────────────────────────────────────────────────────
h1, h2 = st.columns([4, 1])
h1.markdown(f"<div class='cc-eyebrow'>CEO · {esc(qlabel)} ({qstart.strftime('%m.%d')} – {qend.strftime('%m.%d')})</div>"
            f"<div class='cc-title'>장현우 대표 목표관리</div>", unsafe_allow_html=True)
h2.markdown(f"<div class='cc-eyebrow' style='text-align:right'>분기 종료까지</div><div class='cc-dday'>D-{(qend - TODAY).days}</div>",
            unsafe_allow_html=True)

# ── 이번 주 한 가지 ───────────────────────────────────────────
foc = ceo["focus"]
st.markdown(
    "<div class='cc-banner'><div class='k'>이번 주 한 가지 · {a} – {b}</div><div class='h'>{h}</div><div class='s'>{s}</div></div>".format(
        a=week.strftime("%m.%d"), b=(week + timedelta(days=6)).strftime("%m.%d"),
        h=esc(foc["headline"]) if foc else "<span style='opacity:.5'>이번 주 한 가지를 입력하세요</span>",
        s=esc(foc.get("subline") or "") if foc else ""), unsafe_allow_html=True)
with st.expander("✏️ 이번 주 한 가지 입력/수정"):
    with st.form("cc_focus_form"):
        f_head = st.text_input("한 줄 (크게 보이는 문장)", value=foc["headline"] if foc else "")
        f_sub = st.text_input("보조 한 줄", value=(foc.get("subline") or "") if foc else "")
        st.caption("저장 버튼을 누르기 전에는 반영되지 않아요.")
        if st.form_submit_button("저장", type="primary") and f_head.strip():
            _write("이번 주 한 가지를 저장했어요.", lambda: SUPA.table("ceo_okr_weekly_focus").upsert(
                {"week_start": week.isoformat(), "headline": f_head.strip(), "subline": f_sub.strip() or None,
                 "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="week_start").execute())
            st.rerun()

# ══════════════════════════════════════════════════════════════
# ⭐ 1순위: 자체 브랜드(유통 겸비) 런칭
# ══════════════════════════════════════════════════════════════
brand = ceo["brand"]
pre = [t for t in brand if t["kind"] == "prerequisite"]
miles = [t for t in brand if t["kind"] == "milestone"]
tasks = [t for t in brand if t["kind"] == "task"]
pre_done = sum(1 for t in pre if t["status"] in BRAND_DONE_STATES)
task_done = sum(1 for t in tasks if t["status"] in BRAND_DONE_STATES)
blocked = [t for t in brand if t["status"] == "blocked"]
late = [t for t in brand if t["status"] not in BRAND_DONE_STATES and to_date(t.get("due_date")) and to_date(t["due_date"]) < TODAY]
ms_date = min([to_date(t["due_date"]) for t in miles if t.get("due_date") and t["status"] not in BRAND_DONE_STATES], default=None)

rows = ""
for t in pre + miles + tasks:
    dd = to_date(t.get("due_date"))
    st_label, st_color = {"done": ("완료", GREEN), "doing": ("진행", BLUE), "blocked": ("막힘", RED), "todo": ("대기", GRAY)}[t["status"]]
    over = t["status"] not in BRAND_DONE_STATES and dd and dd < TODAY
    kind_label = {"prerequisite": "선행조건", "milestone": "판단일", "task": "할 일"}[t["kind"]]
    due_txt = (f"{dd.strftime('%m.%d')}" + (f" · <b style='color:{RED}'>{(TODAY - dd).days}일 지남</b>" if over else
               f" · D-{(dd - TODAY).days}" if t["status"] not in BRAND_DONE_STATES else "")) if dd else "기한 미정"
    rows += (f"<div class='cc-row'><span><span class='cc-gray'>{kind_label}</span> &nbsp;<span class='n'>{esc(t['title'])}</span>"
             f" <span class='cc-gray'>· {esc(t.get('owner') or '담당 미정')}</span></span>"
             f"<span>{due_txt} &nbsp;{pill(st_label, st_color)}</span></div>")
if not brand:
    rows = "<div class='cc-row cc-gray'><span>아직 입력된 선행조건·할 일이 없어요. 아래 '할 일 추가'에서 하나씩 쌓아 주세요.</span><span>0건</span></div>"

st.markdown(
    "<div class='cc-card priority'><div class='cc-eyebrow' style='color:{b}'>⭐ 1순위 · 다음 성장축</div>"
    "<div class='cc-h2'>자체 브랜드(유통 겸비) 런칭</div>"
    "<div class='cc-sub'>하루빨리 런칭하기 위해 필요한 일을 계속 정리하고, 막히거나 늦은 것을 맨 위에 둡니다.</div>"
    "<div class='cc-grid' style='margin-top:14px'>"
    "<div><div class='cc-big'>{p}<small>/ {pt} 선행조건</small></div></div>"
    "<div><div class='cc-mid'>{dd}</div><div class='cc-sub'>Go / No-Go 판단일</div></div>"
    "<div><div class='cc-mid' style='color:{rc}'>{bl}건</div><div class='cc-sub'>막힘 · 기한 지남 {lt}건</div></div>"
    "<div><div class='cc-mid'>{td}/{tt}</div><div class='cc-sub'>할 일 완료</div></div></div>"
    "<div class='cc-sub' style='margin-top:12px'>참고(DB 현재값): 인플루언서 풀 {pool}명 · 섭외 퍼널 {fun}건</div>"
    "<div style='margin-top:10px'>{rows}</div></div>".format(
        b=BLUE, p=pre_done, pt=len(pre), dd=(f"D-{(ms_date - TODAY).days}" if ms_date else "입력"),
        rc=RED if (blocked or late) else "inherit", bl=len(blocked), lt=len(late), td=task_done, tt=len(tasks),
        pool=ops["pool"], fun=ops["funnel"], rows=rows), unsafe_allow_html=True)

with st.expander("➕ 할 일 추가 / ✏️ 수정"):
    with st.form("cc_brand_add", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([2, 4, 2, 2])
        b_kind = c1.selectbox("종류", ["task", "prerequisite", "milestone"], format_func={"task": "할 일", "prerequisite": "선행조건", "milestone": "판단일"}.get)
        b_title = c2.text_input("내용")
        b_owner = c3.selectbox("담당", STAFF)
        b_due = c4.date_input("기한", value=None)
        st.caption("추가 버튼을 눌러야 저장돼요.")
        if st.form_submit_button("추가", type="primary") and b_title.strip():
            _write("브랜드 런칭 항목을 추가했어요.", lambda: SUPA.table("ceo_brand_launch_tasks").insert(
                {"kind": b_kind, "title": b_title.strip(), "owner": b_owner, "due_date": b_due.isoformat() if b_due else None}).execute())
            st.rerun()
    if brand:
        with st.form("cc_brand_edit"):
            pick = st.selectbox("수정할 항목", [t["id"] for t in brand], format_func=lambda i: next(f"[{t['status']}] {t['title']}" for t in brand if t["id"] == i))
            cur = next(t for t in brand if t["id"] == pick)
            e1, e2, e3 = st.columns(3)
            e_stat = e1.selectbox("상태", ["todo", "doing", "blocked", "done"], index=["todo", "doing", "blocked", "done"].index(cur["status"]),
                                  format_func={"todo": "대기", "doing": "진행", "blocked": "막힘", "done": "완료"}.get)
            e_due = e2.date_input("기한", value=to_date(cur.get("due_date")))
            e_del = e3.checkbox("이 항목 삭제")
            st.caption("저장 버튼을 눌러야 반영돼요. 삭제는 체크한 뒤 저장해야 실행돼요.")
            if st.form_submit_button("저장"):
                if e_del:
                    _write("항목을 삭제했어요.", lambda: SUPA.table("ceo_brand_launch_tasks").delete().eq("id", pick).execute())
                else:
                    _write("항목을 수정했어요.", lambda: SUPA.table("ceo_brand_launch_tasks").update(
                        {"status": e_stat, "due_date": e_due.isoformat() if e_due else None, "updated_at": datetime.now(timezone.utc).isoformat(),
                         "done_at": datetime.now(timezone.utc).isoformat() if e_stat == "done" else None}).eq("id", pick).execute())
                st.rerun()

# ══════════════════════════════════════════════════════════════
# 받을 돈 + 계약예정 — 현황만 보여준다. 확인·처리(받음/부분입금/아직/계약취소)는 CFO > Receivables 에서.
# ══════════════════════════════════════════════════════════════
rc = build_receivables(money["ev"], money["projs"], money["matched_ids"], money["checks"], TODAY)
open_rows = rc["open"]
sm = summarize(open_rows)
total_open, max_over = sm["total"], sm["max_over"]

# 계약예정: 대표님이 대화로 알려주신 추정(submitted_by=장현우)과, 김선재가 계약서·인보이스 기준으로 입력한 정확한 값을 나눠서 본다.
live_deals = [d for d in ops["deals"] if d.get("status") in ("예정", "협의중")]
ceo_deals = [d for d in live_deals if d.get("submitted_by") == "장현우"]
sales_deals = [d for d in live_deals if d.get("submitted_by") != "장현우"]
ceo_deal_sum = sum(float(d.get("expected_amount") or 0) for d in ceo_deals)
sales_deal_sum = sum(float(d.get("expected_amount") or 0) for d in sales_deals)
fc_in = money["forecasts"]
fc_sum = sum(float(f.get("amount") or 0) for f in fc_in)
deal_total = ceo_deal_sum + sales_deal_sum + fc_sum

# 김선재 쪽 계약 데이터 현황(캠페인·계약서·인보이스)
camps = ops["camp"]
camp_contract = sum(1 for c in camps if c.get("contract_url"))
camp_invoice = [c for c in camps if c.get("invoice_amount") is not None]
camp_invoice_sum = sum(float(c.get("invoice_amount") or 0) for c in camp_invoice)

st.markdown("### 💰 받을 돈 · 계약예정")
m1, m2 = st.columns(2)
with m1:
    top_rows = ""
    for r in open_rows[:6]:
        name = r["brand"] or r["title"] or "(이름 없음)"
        ck = r["check"]
        tag = pill("체크 필요", RED) if r["need_check"] else (f"<span class='cc-gray'>{days_ago(ck['checked_at'])}일 전 확인({STATUS_LABEL[ck['status']]})</span>" if ck else "")
        due_txt = (f"<b style='color:{RED}'>{r['overdue_days']}일 경과</b>" if r["overdue_days"] else (f"D-{(r['due_date'] - TODAY).days}" if r["due_date"] else "기한 미정"))
        part = f" <span class='cc-gray'>(예정 {won(r['amount'])} 중 {won(r['received'])} 받음)</span>" if r["received"] else ""
        top_rows += (f"<div class='cc-row'><span class='n'>{esc(name)} <span class='cc-gray'>{esc(r['title'])}</span></span>"
                     f"<span>{won(r['remaining'])}{part} · {due_txt} {tag}</span></div>")
    if not open_rows:
        top_rows = "<div class='cc-row cc-gray'><span>받을 돈(예정) 건이 없어요</span><span>0건</span></div>"
    st.markdown(
        "<div class='cc-card'><div class='cc-eyebrow'>O2 · 현금</div><div class='cc-h2'>받을 돈 (예정)</div>"
        "<div class='cc-big' style='color:{c}'>{v}</div>"
        "<div class='cc-sub'>예정 {n}건 · <b style='color:{c2}'>체크 필요 {nc}건</b> · 기한 경과 {no}건(최대 {mo}일)</div>"
        "<div class='cc-sub'>남은 금액이 큰 순서예요. 부분입금은 남은 금액만, 계약취소·받음은 뺀 값이에요.</div>"
        "<div style='margin-top:10px'>{rows}</div></div>".format(
            c=RED if total_open else "inherit", v=won(total_open) if total_open else "0원", n=sm["n"], c2=RED if sm["need_n"] else GRAY,
            nc=sm["need_n"], no=sm["late_n"], mo=max_over, rows=top_rows), unsafe_allow_html=True)
    try:
        st.page_link("pages/17_수금관리.py", label="💰 확인·처리는 CFO › Receivables 에서 →")
    except Exception:
        st.caption("확인·처리는 상단 메뉴 CFO › Receivables 에서 해요.")
with m2:
    deal_rows = ""
    for d in sorted(live_deals, key=lambda d: -float(d.get("expected_amount") or 0))[:5]:
        src = "대표 메모(추정)" if d.get("submitted_by") == "장현우" else "김선재 입력"
        mon = str(d.get("expected_month") or "")[:7]
        deal_rows += (f"<div class='cc-row'><span class='n'>{esc(d.get('brand_name'))} <span class='cc-gray'>{esc(src)}"
                      f"{' · ' + esc(mon) if mon else ''}{' · ' + esc(d.get('probability')) if d.get('probability') else ''}</span></span>"
                      f"<span>{won(d.get('expected_amount'))}</span></div>")
    st.markdown(
        "<div class='cc-card'><div class='cc-eyebrow'>대표 메모 · 김선재 입력 · 직원 신고</div><div class='cc-h2'>계약예정</div>"
        "<div class='cc-big' style='color:{c}'>{v}</div>"
        "<div class='cc-row'><span>대표 메모(추정, 대화로 입력)</span><span class='n'>{a}건 · {ads}</span></div>"
        "<div class='cc-row'><span>김선재 입력(계약서·인보이스 기준, 정확)</span><span class='n'>{b}건 · {bds}</span></div>"
        "<div class='cc-row'><span>직원 예상입금 신고(미처리)</span><span class='n'>{f}건 · {fs}</span></div>"
        "{deals}"
        "<div class='cc-sub' style='margin-top:10px'>김선재 계약 데이터: 캠페인 {nc}건 · 계약서 첨부 {ct}건 · 인보이스 금액 입력 {ni}/{nc}건"
        "{invsum}</div>"
        "<div class='cc-sub'>{gap}</div><div class='cc-sub'>{wait}</div></div>".format(
            c=BLUE if deal_total else GRAY, v=won(deal_total) if deal_total else "0원",
            a=len(ceo_deals), ads=won(ceo_deal_sum), b=len(sales_deals), bds=won(sales_deal_sum), f=len(fc_in), fs=won(fc_sum),
            deals=deal_rows, nc=len(camps), ct=camp_contract, ni=len(camp_invoice),
            invsum=f" (합계 {won(camp_invoice_sum)})" if camp_invoice else "",
            gap=("인보이스 금액이 입력되면 청구액으로 보여드려요 — 팀스페이스 김선재 업무보고에서 입력." if len(camps) and not camp_invoice else
                 ("" if camps else "아직 등록된 캠페인이 없어요.")),
            wait="" if sales_deals else "김선재 계약예정 입력 대기 — 팀스페이스 업무보고에서 입력하면 여기에 바로 나타나요."), unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════
# SCM 8단계 — 조직도 배치 · 비어있음/막힘/느림
# ══════════════════════════════════════════════════════════════
st.markdown("### 🔗 SCM 8단계 — 바뀌지 않는 핵심 프로세스")
st.caption("단계별로 담당 팀원과 실제 데이터(OKR·KPI·은행·캠페인)를 놓고, 비어있는 곳 · 막힌 곳 · 시간이 느린 곳을 색으로 보여줘요. "
           "신호: 🔴 비어있음/막힘(마감 지남) · 🟠 느림(체크인 14일↑ 없음 과반) · 🟢 정상 · ⚪ 데이터 대기")

items = okr["items"]
by_stage = {c: [] for c, *_ in STAGES}
unmapped = []
for it in items:
    s = stage_of(it, okr["smap"])
    (by_stage[s] if s in by_stage else unmapped).append(it)
logs_by_stage = {c: [0, 0] for c, *_ in STAGES}  # [달성, 전체]
for lg in okr["logs"]:
    s = stage_of({"id": "", "category": lg.get("category"), "title": lg.get("title"), "person": lg.get("person")}, okr["smap"])
    if s in logs_by_stage:
        logs_by_stage[s][1] += 1
        logs_by_stage[s][0] += 1 if lg.get("achieved") else 0

# 실데이터 지표 재료
rev_by_month = {}
for r in money["rev_rows"]:
    k = str(r["txn_date"])[:7]
    rev_by_month[k] = rev_by_month.get(k, 0) + float(r.get("amount") or 0)
rev_this = rev_by_month.get(TODAY.strftime("%Y-%m"), 0)
acc = ops["acc"]
n_active = sum(1 for a in acc if a.get("status") in ("운영중", "계약완료"))
n_neg = sum(1 for a in acc if a.get("status") == "협상중")
camp30 = sum(1 for c in ops["camp"] if to_date(c.get("open_date")) and (TODAY - to_date(c["open_date"])).days <= 30)
n_new30 = sum(1 for a in acc if days_ago(a.get("created_at")) is not None and days_ago(a["created_at"]) <= 30)
sat = [a for a in acc if a.get("satisfaction_score") is not None]
renew30 = sum(1 for a in acc if to_date(a.get("renewal_date")) and 0 <= (to_date(a["renewal_date"]) - TODAY).days <= 30)
no_contact = sum(1 for a in acc if not a.get("last_contact_date") or (TODAY - to_date(a["last_contact_date"])).days > 30)
budget_in = sum(1 for a in acc if float(a.get("monthly_budget") or 0) > 0)
plc = ops["plc"]
plc_done = sum(1 for p in plc if p.get("status") == "업로드완료")
plc_ok = sum(1 for p in plc if p.get("guideline_ok"))
plc_recent = sum(1 for p in plc if days_ago(p.get("updated_at")) is not None and days_ago(p["updated_at"]) <= 14)
act = ops["act"]
fb = sum(1 for a in act if a.get("manager_feedback"))
asg_done = sum(1 for a in ops["asg"] if a.get("status") in ("done", "완료"))
dev_open = sum(1 for d in ops["dev"] if d.get("status") not in ("done", "완료"))
cls_rate = (1 - money["unc90"] / money["tot90"]) if money["tot90"] else None
vis_age = days_ago(ops["vision"][0]["updated_at"]) if ops["vision"] else None


def pct(a, b):
    return f"{a / b * 100:.0f}%" if b else "0%"


def avg_pct(its):
    r = [min(float(i.get("progress") or 0) / float(i["target_qty"]), 1) for i in its if float(i.get("target_qty") or 0) > 0]
    return f"{sum(r) / len(r) * 100:.0f}%" if r else "0%"


def lg(c):
    a, b = logs_by_stage[c]
    return f"{a}/{b}" if b else "이력 없음"


METRICS = {
    "S1": (str(n_neg), "협상중 브랜드(곳)", [("최근 30일 신규 계정", f"{n_new30}곳"), ("계약예정(대표 메모+김선재)", f"{len(live_deals)}건 · {won(ceo_deal_sum + sales_deal_sum)}")], n_neg + n_new30 + len(live_deals) > 0),
    "S2": (won(rev_this) if rev_this else "0원", "이번 달 입금 매출(은행, 매출 계정)", [("운영중 브랜드", f"{n_active}곳"), ("최근 30일 캠페인 등록", f"{camp30}건"), ("월예산 입력된 계정", f"{budget_in}/{len(acc)}")], bool(rev_this or acc)),
    "S3": (pct(plc_done, len(plc)), f"업로드 완료율({plc_done}/{len(plc)}건)", [("진행중(미완료)", f"{len(plc) - plc_done}건"), ("가이드라인 확인", f"{plc_ok}/{len(plc)}"), ("최근 14일 갱신", f"{plc_recent}건")], bool(plc)),
    "S4": (f"{len(sat)}/{len(acc)}", "만족도 점수 입력된 계정", [("30일 넘게 접촉 없음", f"{no_contact}곳"), ("갱신일 30일 이내", f"{renew30}곳"), ("공짜요소·시즌성 부여 기록", "항목 없음(설계 필요)")], bool(sat)),
    "S5": (avg_pct(by_stage["S5"]), "자동화 OKR 평균 달성률", [("개발 할 일 미완료", f"{dev_open}건"), ("지난 KPI 기간 달성", lg("S5"))], bool(by_stage["S5"]) or bool(ops["dev"])),
    "S6": (pct(money["tot90"] - money["unc90"], money["tot90"]) if cls_rate is not None else "0%", "은행거래 분류율(90일)", [("미분류 거래", f"{money['unc90']}건"), ("받을 돈 체크 필요", f"{sm['need_n']}건"), ("기한 경과 미수", f"{sm['late_n']}건")], bool(money["tot90"])),
    "S7": (f"{len(act)}건", "최근 14일 업무기록", [("대표 피드백 달린 기록", f"{fb}건"), ("대표 할일 완료", f"{asg_done}/{len(ops['asg'])}"), ("회의록(30일)", f"{ops['meet']}건"), ("지난 KPI 기간 달성", lg("S7"))], bool(act or ops["meet"])),
    "S8": ("입력", "IR 지표 정의 필요", [("회사 비전 마지막 수정", f"{vis_age}일 전" if vis_age is not None else "기록 없음"), ("1~7단계 핵심 숫자", "IR 재료로 쌓는 중")], False),
}

cards = ""
for code, name, en, desc in STAGES:
    its = by_stage[code]
    sp = stage_speed(its)
    owners = sorted({i["person"] for i in its})
    hl, hll, lines, has_data = METRICS[code]
    status = stage_status(sp, bool(owners), has_data)
    label, color = STATUS_VIEW[status]
    own_html = "".join(f"<span class='cc-chip'>{esc(o)} {sum(1 for i in its if i['person'] == o)}</span>" for o in owners) \
        or f"<span class='cc-chip' style='border-color:{RED};color:{RED}'>담당 없음</span>"
    speed = (f"마감 지남 {sp['overdue']} · 체크인 14일↑ 없음 {sp['stale']}/{sp['active']}" + (f" · 가장 오래 {sp['oldest']}일" if sp["oldest"] is not None else "")) if sp["n"] else "연결된 OKR 항목 없음"
    ln_html = "".join(f"<div class='ln'><span>{esc(a)}</span><b>{esc(b)}</b></div>" for a, b in lines)
    cards += (f"<div class='cc-stage' style='border-left-color:{color}'>"
              f"<div class='no'>STEP {code[1]} &nbsp;{pill(label, color)}</div>"
              f"<div class='nm'>{esc(name)}<span class='en'>{esc(en)}</span></div>"
              f"<div class='cc-sub' style='font-size:13px'>{esc(desc)}</div>"
              f"<div class='hl'>{esc(hl)}</div><div class='hll'>{esc(hll)}</div>"
              f"{ln_html}"
              f"<div class='own'>{own_html}</div>"
              f"<div class='ln' style='border-top:1px solid rgba(128,128,128,.25)'><span>⏱ 속도</span><span style='font-size:12px;text-align:right'>{esc(speed)}</span></div>"
              f"</div>")
st.markdown(f"<div class='cc-grid'>{cards}</div>", unsafe_allow_html=True)

if okr["errs"]:
    st.warning("일부 OKR 데이터를 읽지 못했어요: " + " / ".join(okr["errs"]))
if unmapped:
    st.warning(f"⚠️ 단계가 정해지지 않은 OKR 항목 {len(unmapped)}건: " + ", ".join(sorted({f"{i['person']} {i.get('category') or i['title'][:12]}" for i in unmapped})[:8]))
with st.expander("🧩 OKR 분류를 SCM 단계에 배치/수정"):
    cats_all = sorted({i.get("category") or "(분류 없음)" for i in items})
    cur_stage = {}
    for c in cats_all:
        sample = next(i for i in items if (i.get("category") or "(분류 없음)") == c)
        cur_stage[c] = stage_of(sample, okr["smap"])
    with st.form("cc_scm_map"):
        cc1, cc2 = st.columns(2)
        m_cat = cc1.selectbox("OKR 분류", cats_all, format_func=lambda c: f"{c}  (지금: {STAGE_NAME.get(cur_stage[c], '미지정')})")
        m_stage = cc2.selectbox("배치할 단계", [c for c, *_ in STAGES], format_func=lambda c: f"{c[1]}. {STAGE_NAME[c]}")
        st.caption("저장해야 반영돼요. 이 분류의 모든 항목이 선택한 단계로 묶여요.")
        if st.form_submit_button("배치 저장"):
            _write("단계 배치를 저장했어요.", lambda: SUPA.table("ceo_scm_stage_map").upsert(
                {"match_kind": "category", "match_value": "" if m_cat == "(분류 없음)" else m_cat, "stage_code": m_stage,
                 "updated_at": datetime.now(timezone.utc).isoformat()}, on_conflict="match_kind,match_value").execute())
            st.rerun()

# ══════════════════════════════════════════════════════════════
# O1 ~ O4 카드
# ══════════════════════════════════════════════════════════════
st.markdown(f"### 🎯 {qlabel} OKR")
AUTO = {
    "KR2.1": (total_open, "원", None),
    "KR2.2": (max_over, "일", None),
    "KR3.1": (rev_this, "원", None),
    "KR3.3": (n_active, "곳", None),
}


def kr_view(k):
    """KR 한 줄을 display_style 에 맞게 그린 HTML. 값이 없으면 회색 '입력'."""
    code = k["code"]
    auto = k["source"] == "auto" and code in AUTO
    val = AUTO[code][0] if auto else k.get("current_value")
    tgt = k.get("target_value")
    due = to_date(k.get("due_date"))
    style = k.get("display_style") or "bignum"
    st_color = {"red": RED, "amber": AMBER, "green": GREEN}.get(k.get("status"), None)
    title = f"<div style='font-size:16px;font-weight:700;margin-top:10px'>{esc(code)} · {esc(k['title'])}</div>"
    sub = f"<div class='cc-sub' style='font-size:14px'>{esc(k.get('note') or '')}</div>"
    if k["metric_type"] == "date":
        body = f"<div class='cc-mid' style='color:{BLUE}'>{'D-' + str((due - TODAY).days) if due and due >= TODAY else ('D+' + str((TODAY - due).days) if due else '입력')}</div>"
    elif val is None:
        body = f"<div class='cc-mid cc-gray'>입력</div>"
    elif style == "dots" or k["metric_type"] == "check":
        n = int(tgt or 0)
        body = "<div class='cc-dots'>" + "".join(f"<span class='{'on' if i < int(val) else ''}'></span>" for i in range(n)) + f"</div><div class='cc-sub'>{int(val)}/{n}</div>"
    elif style == "bar" and tgt:
        body = f"<div class='cc-mid'>{val:,.0f}<small class='cc-sub'> / {float(tgt):,.0f}</small></div><div class='cc-bar'><i style='width:{min(float(val) / float(tgt), 1) * 100:.0f}%'></i></div>"
    elif style == "donut" and tgt:
        p = min(float(val) / float(tgt), 1) * 100
        body = f"<div class='cc-donut' style='background:conic-gradient({GREEN} {p}%, rgba(128,128,128,.25) 0)'><span>{p:.0f}%</span></div>"
    elif style == "trend":
        months = sorted(rev_by_month)[-4:]
        mx = max([rev_by_month[m] for m in months], default=0) or 1
        bars = "".join(f"<div><b style='height:{max(rev_by_month[m] / mx * 70, 3):.0f}px'></b>{m[5:]}월<br>{won(rev_by_month[m])}</div>" for m in months)
        body = f"<div class='cc-mid'>{won(val)}</div><div class='cc-trend'>{bars}</div>"
    else:
        unit = AUTO[code][1] if auto else ""
        shown = won(val) if (auto and unit == "원") else f"{val:,.0f}{unit if unit != '원' else ''}"
        body = f"<div class='cc-mid' style='color:{st_color or 'inherit'}'>{shown}</div>"
    if tgt is not None and style not in ("bar", "dots") and k["metric_type"] != "date":
        sub += f"<div class='cc-sub' style='font-size:14px'>목표 {won(tgt) if (code == 'KR3.1') else f'{float(tgt):,.0f}'}" + (f" · {due.strftime('%m.%d')}까지" if due else "") + "</div>"
    elif due and k["metric_type"] != "date":
        sub += f"<div class='cc-sub' style='font-size:14px'>{due.strftime('%m.%d')}까지</div>"
    return title + body + sub


if not ceo["objs"]:
    st.markdown("<div class='cc-card cc-gray'>이번 분기 OKR이 아직 DB에 없어요. `migrations/20261009_ceo_command_center_seed.sql` 을 적용하면 O1~O4 초안이 들어와요 (값은 모두 '입력'으로 시작).</div>", unsafe_allow_html=True)
else:
    colsO = st.columns(2)
    for idx, o in enumerate(ceo["objs"]):
        krs_o = [k for k in ceo["krs"] if k["objective_id"] == o["id"]]
        with colsO[idx % 2]:
            st.markdown(f"<div class='cc-card'><div class='cc-eyebrow'>{esc(o['code'])} · {esc(o.get('tag') or '')}</div>"
                        f"<div class='cc-h2'>{esc(o['title'])}</div>" + "".join(kr_view(k) for k in krs_o) + "</div>", unsafe_allow_html=True)

    manual = [k for k in ceo["krs"] if k["source"] == "manual"]
    if manual:
        with st.expander("✏️ KR 값 입력/수정 (수동 KR)"):
            with st.form("cc_kr_form"):
                kk = st.selectbox("KR", [k["id"] for k in manual], format_func=lambda i: next(f"{k['code']} · {k['title']}" for k in manual if k["id"] == i))
                kcur = next(k for k in manual if k["id"] == kk)
                f1, f2, f3 = st.columns(3)
                kval = f1.text_input("현재값 (숫자, 비우면 '입력' 상태)", value="" if kcur.get("current_value") is None else f"{float(kcur['current_value']):g}")
                kstat = f2.selectbox("상태", ["(없음)", "red", "amber", "green"], index=["(없음)", "red", "amber", "green"].index(kcur.get("status") or "(없음)"),
                                     format_func={"(없음)": "표시 안 함", "red": "🔴 문제", "amber": "🟠 주의", "green": "🟢 정상"}.get)
                knote = f3.text_input("메모", value=kcur.get("note") or "")
                st.caption("저장 버튼을 눌러야 반영돼요. 값은 이력(ceo_okr_checkins)에도 남아요.")
                if st.form_submit_button("저장", type="primary"):
                    try:
                        newv = float(kval.replace(",", "")) if kval.strip() else None
                    except ValueError:
                        newv = None
                        flash("현재값은 숫자로 입력해주세요.", False)
                    else:
                        def _save_kr():
                            SUPA.table("ceo_okr_krs").update({"current_value": newv, "status": None if kstat == "(없음)" else kstat, "note": knote.strip() or None,
                                                              "updated_at": datetime.now(timezone.utc).isoformat()}).eq("id", kk).execute()
                            SUPA.table("ceo_okr_checkins").insert({"kr_id": kk, "value": newv, "status": None if kstat == "(없음)" else kstat, "note": knote.strip() or None}).execute()
                        _write("KR 값을 저장했어요.", _save_kr)
                    st.rerun()

st.caption(f"기준일 {TODAY.isoformat()} (한국 시간) · 60초마다 DB에서 다시 읽어요 · 숫자는 모두 DB 실제값이며, 값이 없으면 0 또는 '입력'으로 표시합니다.")
