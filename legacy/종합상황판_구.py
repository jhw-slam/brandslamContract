import os
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="종합상황판", layout="wide")

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

STAGES = [("LEAD", "제안·미팅"), ("SENT", "계약서 발송"), ("SIGNED", "서명 완료"),
          ("DEPOSIT", "선금 입금"), ("PROGRESS", "캠페인 진행"), ("BALANCE", "잔금 청구"),
          ("SETTLED", "정산 완료")]
KEYS = [k for k, _ in STAGES]; LABEL = dict(STAGES)
# 단계별 "정체 경고" 임계 일수 (이 일수 넘게 같은 단계면 경고)
STALL = {"LEAD": 14, "SENT": 7, "SIGNED": 7, "DEPOSIT": 5, "PROGRESS": 60, "BALANCE": 10, "SETTLED": 99999}

won = lambda n: "₩{:,}".format(int(n or 0))
def won_short(n):
    n = int(n or 0)
    if n >= 100000000:
        return f"{n/100000000:.1f}억"
    if n >= 10000:
        return f"{n//10000:,}만"
    return f"{n:,}"

def days_since(ts):
    if not ts:
        return None
    try:
        d = pd.to_datetime(ts).tz_localize(None)
        return (datetime.now() - d.to_pydatetime()).days
    except Exception:
        return None

def stage_bar(idx):
    seg = ""
    for n in range(len(STAGES)):
        c = "#2f6df0" if n <= idx else "#e3e7ee"
        seg += f"<span style='display:inline-block;width:12.5%;height:7px;background:{c};margin-right:2px;border-radius:3px'></span>"
    return seg

# ── 데이터 로드 (실데이터 / 데모) ─────────────────────────────
def load_real():
    projs = SUPA.table("projects").select("*").order("created_at").execute().data
    comp = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    for p in projs:
        p["company"] = comp.get(p["company_id"], p.get("brand"))
    cash = SUPA.table("cash_events").select("*").execute().data
    contracts = SUPA.table("contracts").select("id,project_id,doc_type,created_at").execute().data
    try:
        camp_n = SUPA.table("campaigns").select("id", count="exact").execute().count or 0
    except Exception:
        camp_n = 0
    return projs, cash, contracts, camp_n

def load_demo():
    today = date.today()
    def d(n): return (today - timedelta(days=n)).isoformat()
    projs = [
        {"id": "d1", "company": "OWM", "brand": "OWM", "product": "그랜드오프닝 시딩", "stage": "PROGRESS",
         "supply_amount": 90000000, "total_amount": 99000000, "deposit_amount": 49500000, "balance_amount": 49500000,
         "deposit_paid": True, "balance_paid": False, "created_at": d(70)},
        {"id": "d2", "company": "23yearsold", "brand": "23yearsold", "product": "Japan 시딩", "stage": "SIGNED",
         "supply_amount": 30000000, "total_amount": 33000000, "deposit_amount": 16500000, "balance_amount": 16500000,
         "deposit_paid": False, "balance_paid": False, "created_at": d(20)},
        {"id": "d3", "company": "Boosteone", "brand": "Boosteone", "product": "US 벌크 시딩", "stage": "DEPOSIT",
         "supply_amount": 9000000, "total_amount": 9900000, "deposit_amount": 4950000, "balance_amount": 4950000,
         "deposit_paid": True, "balance_paid": False, "created_at": d(9)},
        {"id": "d4", "company": "Farmskin", "brand": "Farmskin", "product": "US 시딩", "stage": "SENT",
         "supply_amount": 12000000, "total_amount": 13200000, "deposit_amount": 6600000, "balance_amount": 6600000,
         "deposit_paid": False, "balance_paid": False, "created_at": d(18)},
        {"id": "d5", "company": "그리니컬", "brand": "그리니컬", "product": "마스크팩", "stage": "LEAD",
         "supply_amount": 0, "total_amount": 0, "deposit_amount": 0, "balance_amount": 0,
         "deposit_paid": False, "balance_paid": False, "created_at": d(33)},
        {"id": "d6", "company": "달바", "brand": "달바", "product": "글로벌 캠페인", "stage": "BALANCE",
         "supply_amount": 40000000, "total_amount": 44000000, "deposit_amount": 22000000, "balance_amount": 22000000,
         "deposit_paid": True, "balance_paid": False, "created_at": d(15)},
        {"id": "d7", "company": "코코스타", "brand": "코코스타", "product": "리뷰 시딩", "stage": "SETTLED",
         "supply_amount": 8000000, "total_amount": 8800000, "deposit_amount": 4400000, "balance_amount": 4400000,
         "deposit_paid": True, "balance_paid": True, "created_at": d(120)},
    ]
    cash = [
        {"project_id": "d1", "direction": "in", "amount": 49500000, "due_date": d(40), "paid": True},
        {"project_id": "d1", "direction": "in", "amount": 49500000, "due_date": d(-10), "paid": False},
        {"project_id": "d1", "direction": "out", "amount": 30000000, "due_date": d(-5), "paid": False},
        {"project_id": "d2", "direction": "in", "amount": 16500000, "due_date": d(-3), "paid": False},
        {"project_id": "d3", "direction": "in", "amount": 4950000, "due_date": d(2), "paid": True},
        {"project_id": "d3", "direction": "out", "amount": 3000000, "due_date": d(20), "paid": False},
        {"project_id": "d6", "direction": "in", "amount": 22000000, "due_date": d(-1), "paid": False},
        {"project_id": "d6", "direction": "out", "amount": 15000000, "due_date": d(12), "paid": False},
        # ── 과거 분기(전년 대비/전분기 대비 데모용) ──
        {"project_id": "d7", "direction": "in", "amount": 18000000, "due_date": "2025-02-15", "paid": True},
        {"project_id": "d7", "direction": "out", "amount": 9000000, "due_date": "2025-02-20", "paid": True},
        {"project_id": "d2", "direction": "in", "amount": 24000000, "due_date": "2025-05-18", "paid": True},
        {"project_id": "d2", "direction": "out", "amount": 11000000, "due_date": "2025-05-25", "paid": True},
        {"project_id": "d1", "direction": "in", "amount": 30000000, "due_date": "2025-08-12", "paid": True},
        {"project_id": "d1", "direction": "out", "amount": 16000000, "due_date": "2025-08-20", "paid": True},
        {"project_id": "d6", "direction": "in", "amount": 41000000, "due_date": "2025-11-10", "paid": True},
        {"project_id": "d6", "direction": "out", "amount": 20000000, "due_date": "2025-11-18", "paid": True},
        {"project_id": "d3", "direction": "in", "amount": 33000000, "due_date": "2026-02-12", "paid": True},
        {"project_id": "d3", "direction": "out", "amount": 14000000, "due_date": "2026-02-19", "paid": True},
    ]
    contracts = [{"id": "c1", "project_id": "d1", "doc_type": "brand", "created_at": d(60)},
                 {"id": "c2", "project_id": "d6", "doc_type": "creator", "created_at": d(14)}]
    return projs, cash, contracts, 9

# ── 헤더 + 데모 토글 ──────────────────────────────────────────
top = st.columns([3, 1.4])
top[0].title("종합상황판")
real_projs, real_cash, real_contracts, camp_n = load_real()
auto_demo = len(real_projs) == 0
demo = top[1].toggle("예시(데모) 데이터로 미리보기", value=auto_demo,
                     help="실제 데이터가 없을 때 화면 구성을 미리 봅니다. 데이터가 쌓이면 끄세요.")

if demo:
    projs, cash, contracts, camp_n = load_demo()
    st.info("🔮 예시(데모) 데이터입니다. 실제 계약·송금 데이터가 쌓이면 이 화면이 그대로 자동으로 채워집니다.")
else:
    projs, cash, contracts = real_projs, real_cash, real_contracts

if not projs:
    st.warning("아직 계약(projects) 데이터가 없습니다. 위 '예시 데이터' 토글로 미리 보거나, '계약 콘솔'에서 계약을 추가하세요.")
    st.stop()

today = date.today()
active = [p for p in projs if p["stage"] != "SETTLED"]

# cash 분석용 DataFrame
cdf = pd.DataFrame(cash) if cash else pd.DataFrame(columns=["project_id", "direction", "amount", "due_date", "paid"])
if not cdf.empty:
    cdf["amount"] = pd.to_numeric(cdf["amount"], errors="coerce").fillna(0)
    cdf["due"] = pd.to_datetime(cdf["due_date"], errors="coerce")

def cash_sum(direction, paid=None, overdue=False, this_month=False):
    if cdf.empty:
        return 0
    m = cdf["direction"] == direction
    if paid is not None:
        m &= cdf["paid"] == paid
    if overdue:
        m &= (cdf["due"].dt.date < today)
    if this_month:
        m &= (cdf["due"].dt.year == today.year) & (cdf["due"].dt.month == today.month)
    return int(cdf[m]["amount"].sum())

# ── 1) 핵심 지표 ──────────────────────────────────────────────
k = st.columns(5)
k[0].metric("진행 중 계약", f"{len(active)}건", help="정산완료 제외")
k[1].metric("진행 계약금액(VAT포함)", won_short(sum(p.get("total_amount") or 0 for p in active)))
k[2].metric("미수금(받을)", won_short(cash_sum("in", paid=False)))
k[3].metric("미지급(나갈)", won_short(cash_sum("out", paid=False)))
od = cash_sum("in", paid=False, overdue=True)
k[4].metric("연체 입금(기일 경과)", won_short(od), delta="확인 필요" if od else None, delta_color="inverse")

# ── 2) 주의가 필요한 계약 (경고) ──────────────────────────────
st.subheader("⚠️ 지금 챙겨야 할 것")
alerts = []
for p in projs:
    if p["stage"] == "SETTLED":
        continue
    ds = days_since(p.get("updated_at") or p.get("created_at"))
    th = STALL.get(p["stage"], 30)
    if ds is not None and ds > th:
        sev = "🔴" if ds > th * 2 else "🟠"
        alerts.append((sev, ds, f"{p['company']} · {p.get('product') or p['brand']}",
                       f"[{LABEL[p['stage']]}] 단계에서 {ds}일째 정체 (기준 {th}일) → 담당자 확인 필요"))
# 연체 입금/지급
if not cdf.empty:
    for _, r in cdf[(cdf["paid"] == False) & (cdf["due"].dt.date < today)].iterrows():
        nm = next((p for p in projs if p["id"] == r["project_id"]), None)
        nm = (nm["company"] if nm else "미연결")
        late = (today - r["due"].date()).days
        kind = "입금 지연" if r["direction"] == "in" else "지급 지연"
        alerts.append(("🔴", late, f"{nm}", f"{kind} {won_short(r['amount'])} · 기일 {late}일 경과"))

if alerts:
    alerts.sort(key=lambda x: -x[1])
    for sev, _, who, why in alerts[:12]:
        st.markdown(f"{sev} **{who}** — {why}")
else:
    st.success("정체·연체 항목이 없습니다. 모든 계약이 정상 흐름이에요.")

# ── 2.5) 수금 현황 (입금 / 선작업 미입금 / 미수 대기 분리) ─────
st.subheader("💸 수금 현황 — 입금됨 vs 일 먼저 진행 중(미입금)")
NEXT = {"LEAD": "계약서 발송", "SENT": "서명 받기", "SIGNED": "선금 청구·입금 확인",
        "DEPOSIT": "캠페인 진행", "PROGRESS": "성과보고서 업로드 → 잔금 청구",
        "BALANCE": "잔금 입금 확인", "SETTLED": "완료"}
WORK = ("PROGRESS", "BALANCE", "SETTLED")  # 사실상 일이 진행된 단계

byp = {}
if not cdf.empty:
    for pid, g in cdf[cdf["direction"] == "in"].groupby("project_id"):
        byp[pid] = {"paid": int(g[g["paid"] == True]["amount"].sum()),
                    "unpaid": int(g[g["paid"] == False]["amount"].sum())}

def report_link(p):
    return f"  ·  [📑 성과보고서]({p['report_url']})" if p.get("report_url") else ""

paid_list, risk_list, wait_list = [], [], []
for p in projs:
    a = byp.get(p["id"], {"paid": 0, "unpaid": 0})
    if a["unpaid"] > 0 and p["stage"] in WORK:
        risk_list.append((p, a))
    elif a["unpaid"] > 0:
        wait_list.append((p, a))
    if a["paid"] > 0:
        paid_list.append((p, a))

sc = st.columns(3)
sc[0].metric("✅ 수금 완료", f"{len(paid_list)}건", won_short(sum(a['paid'] for _, a in paid_list)))
sc[1].metric("🔴 선작업·미입금 (최우선)", f"{len(risk_list)}건", "−" + won_short(sum(a['unpaid'] for _, a in risk_list)), delta_color="inverse")
sc[2].metric("🟠 미수 대기(초기단계)", f"{len(wait_list)}건", "−" + won_short(sum(a['unpaid'] for _, a in wait_list)), delta_color="inverse")

with st.expander(f"🔴 일은 진행 중인데 아직 미입금 — {len(risk_list)}건 (가장 위험·최우선 회수)", expanded=bool(risk_list)):
    if risk_list:
        for p, a in sorted(risk_list, key=lambda x: -x[1]["unpaid"]):
            st.markdown(f"🔴 **{p['company']} · {p.get('product') or p['brand']}** — 미입금 **{won(a['unpaid'])}** · "
                        f"현재 [{LABEL[p['stage']]}] → 다음 할 일: **{NEXT.get(p['stage'],'-')}**" + report_link(p))
    else:
        st.caption("해당 없음 — 진행 중 프로젝트는 모두 입금 확인됨.")

with st.expander(f"📋 받을 돈 미수 — 잔금까지 가는 체크리스트 (총 {len(risk_list)+len(wait_list)}건)"):
    allunpaid = sorted(risk_list + wait_list, key=lambda x: KEYS.index(x[0]["stage"]), reverse=True)
    if allunpaid:
        for p, a in allunpaid:
            flag = "🔴" if p["stage"] in WORK else "🟠"
            st.markdown(f"{flag} **{p['company']} · {p.get('product') or p['brand']}** — 미수 {won(a['unpaid'])} · "
                        f"[{LABEL[p['stage']]}] → **{NEXT.get(p['stage'],'-')}**" + report_link(p))
    else:
        st.caption("미수 항목이 없습니다.")

with st.expander(f"✅ 수금 완료 프로젝트 — {len(paid_list)}건"):
    if paid_list:
        for p, a in sorted(paid_list, key=lambda x: -x[1]["paid"]):
            rem = f" · 잔여 미입금 {won(a['unpaid'])}" if a["unpaid"] else ""
            st.markdown(f"✅ **{p['company']} · {p.get('product') or p['brand']}** — 수금 {won(a['paid'])}{rem}" + report_link(p))
    else:
        st.caption("아직 수금 완료된 건이 없습니다.")

# ── 3) 파이프라인 단계 분포 ───────────────────────────────────
st.subheader("📊 파이프라인 단계 분포")
counts = {lab: 0 for _, lab in STAGES}
for p in projs:
    counts[LABEL[p["stage"]]] += 1
pipe = pd.DataFrame({"단계": list(counts.keys()), "건수": list(counts.values())}).set_index("단계")
st.bar_chart(pipe, height=240)

# ── 4) 재무 시각화 (송금 캘린더 기반) ─────────────────────────
st.subheader("💰 월별 자금 흐름 (받을/나갈)")
if not cdf.empty:
    tmp = cdf.dropna(subset=["due"]).copy()
    tmp["월"] = tmp["due"].dt.strftime("%Y-%m")
    piv = tmp.pivot_table(index="월", columns="direction", values="amount", aggfunc="sum", fill_value=0)
    piv = piv.rename(columns={"in": "받을 돈", "out": "나갈 돈"})
    st.bar_chart(piv, height=260)

    fc = st.columns(2)
    fc[0].markdown("**미수금 Top (받을 돈)**")
    inb = tmp[(tmp["direction"] == "in") & (tmp["paid"] == False)].groupby("project_id")["amount"].sum().sort_values(ascending=False)
    if len(inb):
        for pid, amt in inb.head(5).items():
            nm = next((p["company"] for p in projs if p["id"] == pid), "미연결")
            fc[0].write(f"· {nm} — {won(amt)}")
    else:
        fc[0].caption("미수금 없음")
    fc[1].markdown("**미지급 Top (나갈 돈)**")
    outb = tmp[(tmp["direction"] == "out") & (tmp["paid"] == False)].groupby("project_id")["amount"].sum().sort_values(ascending=False)
    if len(outb):
        for pid, amt in outb.head(5).items():
            nm = next((p["company"] for p in projs if p["id"] == pid), "미연결")
            fc[1].write(f"· {nm} — {won(amt)}")
    else:
        fc[1].caption("미지급 없음")
else:
    st.caption("송금 일정 데이터가 없습니다. '계약 콘솔'의 송금 일정이나 '송금 캘린더'에서 입력하면 여기에 자금 흐름이 그려집니다.")

# ── 5) 계약별 진행 현황 ───────────────────────────────────────
st.subheader("📋 계약별 진행 현황")
order = {k: i for i, k in enumerate(KEYS)}
for p in sorted(projs, key=lambda x: order.get(x["stage"], 0)):
    ds = days_since(p.get("updated_at") or p.get("created_at"))
    th = STALL.get(p["stage"], 30)
    badge = ""
    if p["stage"] != "SETTLED" and ds is not None and ds > th:
        badge = " 🔴" if ds > th * 2 else " 🟠"
    pid = p["id"]
    out_in = int(cdf[(cdf["project_id"] == pid) & (cdf["direction"] == "in") & (cdf["paid"] == False)]["amount"].sum()) if not cdf.empty else 0
    html = (f"<div style='border:1px solid #eef0f4;border-radius:10px;padding:10px 14px;margin-bottom:8px'>"
            f"<div style='display:flex;justify-content:space-between'>"
            f"<b>{p['company']} · {p.get('product') or p['brand']}{badge}</b>"
            f"<span style='color:#666'>{LABEL[p['stage']]} · {('정체 '+str(ds)+'일' ) if (ds is not None and p['stage']!='SETTLED' and ds>th) else (str(ds)+'일 전 활동' if ds is not None else '')}</span></div>"
            f"<div style='margin:6px 0'>{stage_bar(order.get(p['stage'],0))}</div>"
            f"<div style='color:#444;font-size:13px'>총액 {won(p.get('total_amount'))}"
            f"{' · 받을 잔액 '+won(out_in) if out_in else ''}"
            f"{' · 선금완료' if p.get('deposit_paid') else ''}</div>"
            f"</div>")
    st.markdown(html, unsafe_allow_html=True)

# ── 6) 재무 분석 (약식) ───────────────────────────────────────
st.divider()
st.subheader("🧾 재무 분석 (약식)")

rev = cash_sum("in"); cost = cash_sum("out")
gp = rev - cost; rate = round(gp / rev * 100) if rev else 0
recv = int(cdf[(cdf["direction"] == "in") & (cdf["paid"] == True)]["amount"].sum()) if not cdf.empty else 0
paidout = int(cdf[(cdf["direction"] == "out") & (cdf["paid"] == True)]["amount"].sum()) if not cdf.empty else 0
ar = cash_sum("in", paid=False); ap = cash_sum("out", paid=False)

st.markdown("**약식 손익 (발생 기준 · 받을/나갈 합계)**")
pl = st.columns(4)
pl[0].metric("매출액", won_short(rev))
pl[1].metric("비용", won_short(cost))
pl[2].metric("매출총이익", won_short(gp))
pl[3].metric("이익률", f"{rate}%")

st.markdown("**현금 · 채권/채무 (수금 기준)**")
cf = st.columns(4)
cf[0].metric("수금완료(입금)", won_short(recv))
cf[1].metric("지급완료", won_short(paidout))
cf[2].metric("미수금(받을)", won_short(ar))
cf[3].metric("미지급금(나갈)", won_short(ap))

# 분기별 매출 추이 + QoQ / YoY
st.markdown("**분기별 매출 추이 · QoQ / YoY 성장률**")
if not cdf.empty:
    inq = cdf[cdf["direction"] == "in"].dropna(subset=["due"]).copy()
    if len(inq):
        inq["P"] = inq["due"].dt.to_period("Q")
        q = inq.groupby("P")["amount"].sum().sort_index()
        full = pd.period_range(q.index.min(), q.index.max(), freq="Q")
        q = q.reindex(full, fill_value=0)
        qoq = q.pct_change() * 100
        yoy = (q / q.shift(4) - 1) * 100
        idx = [str(x) for x in q.index]
        st.bar_chart(pd.Series(q.values, index=idx), height=220)
        qt = pd.DataFrame({
            "분기": idx, "매출": [won(v) for v in q.values],
            "QoQ%": ["-" if pd.isna(v) else f"{v:+.1f}%" for v in qoq.values],
            "YoY%": ["-" if pd.isna(v) else f"{v:+.1f}%" for v in yoy.values],
        })
        st.dataframe(qt, use_container_width=True, hide_index=True)
    else:
        st.caption("매출(받을 돈) 데이터가 없습니다.")
else:
    st.caption("송금 데이터가 쌓이면 분기별 성장률이 표시됩니다.")

# 업체별 매출 & 성장률 (최근 분기 vs 직전 분기)
st.markdown("**업체별 매출 · 성장률**")
if not cdf.empty:
    pid2comp = {p["id"]: p["company"] for p in projs}
    inc = cdf[cdf["direction"] == "in"].dropna(subset=["due"]).copy()
    inc["company"] = inc["project_id"].map(pid2comp).fillna("미연결")
    if len(inc):
        inc["P"] = inc["due"].dt.to_period("Q")
        piv = inc.pivot_table(index="company", columns="P", values="amount", aggfunc="sum", fill_value=0).sort_index(axis=1)
        cols_q = list(piv.columns)
        last_q = cols_q[-1]; prev_q = cols_q[-2] if len(cols_q) >= 2 else None
        rows = []
        for comp in piv.index:
            total = int(piv.loc[comp].sum())
            cur = int(piv.loc[comp, last_q])
            if prev_q is not None and piv.loc[comp, prev_q]:
                g = f"{(cur/piv.loc[comp, prev_q]-1)*100:+.1f}%"
            else:
                g = "-"
            rows.append({"업체": comp, "누적 매출": won(total), f"최근({last_q})": won(cur), "직전분기 대비": g})
        st.dataframe(pd.DataFrame(rows).sort_values("누적 매출", ascending=False), use_container_width=True, hide_index=True)
    else:
        st.caption("업체별 매출 데이터가 없습니다.")
else:
    st.caption("송금 데이터가 쌓이면 업체별 성장률이 표시됩니다.")

st.caption("※ 약식 분석입니다(부가세·세금·고정비 미반영). 정식 재무제표 대용이 아니라 운영 모니터링용이에요.")

st.divider()
st.caption(f"사이트 주문(campaigns) {camp_n}건 · 저장된 계약서 {len(contracts)}건"
           + ("  ·  🔮 예시 데이터" if demo else ""))
