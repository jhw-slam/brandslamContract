import os
from datetime import date

import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="영업", layout="wide")

# ── 비밀번호 게이트 (기존 페이지와 동일) ─────────────────────────
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

# sales_brands.company_id 로 기존 계약시스템의 companies 테이블과 연결한다
TABLE_SQL = """
create table if not exists sales_brands (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  company_id uuid references companies(id),   -- 기존 계약시스템(companies)과 연동
  tier text default '일반',              -- '전략' / '일반'
  monthly_budget numeric default 0,       -- 마케팅비 (자동 데이터 없을 때의 수동값)
  marketing_plan text,
  status text default '정상',             -- '정상' / '주의' / '이탈위험'
  satisfaction text default '미확인',      -- '만족' / '보통' / '불만족' / '미확인'
  last_meeting_date date,
  meeting_notes text,
  created_at timestamptz default now()
);

create table if not exists sales_revenue_monthly (
  id uuid primary key default gen_random_uuid(),
  brand_id uuid references sales_brands(id) on delete cascade,
  month date not null,                    -- 매출 발생 월 (예: 2026-07-01)
  revenue numeric default 0,              -- OWM 매출 (외부 데이터, 수동 업로드)
  created_at timestamptz default now()
);
""".strip()

# 이미 sales_brands를 만든 상태라면 이 한 줄만 추가로 실행하면 됨
ALTER_SQL = "alter table sales_brands add column if not exists company_id uuid references companies(id);"

def tables_exist():
    try:
        SUPA.table("sales_brands").select("id, company_id").limit(1).execute()
        SUPA.table("sales_revenue_monthly").select("id").limit(1).execute()
        return True
    except Exception:
        return False

def create_tables_via_db():
    db_url = os.environ.get("SUPABASE_DB_URL")
    if not db_url:
        return False, "missing_url"
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(TABLE_SQL)
            cur.execute(ALTER_SQL)
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)

def load_brands():
    try:
        data = SUPA.table("sales_brands").select("*").order("created_at").execute().data
        df = pd.DataFrame(data)
    except Exception:
        df = pd.DataFrame()
    for col in ["id", "name", "company_id", "tier", "monthly_budget", "marketing_plan",
                "status", "satisfaction", "last_meeting_date", "meeting_notes"]:
        if col not in df.columns:
            df[col] = None
    return df

def load_revenue():
    try:
        data = SUPA.table("sales_revenue_monthly").select("*").execute().data
        df = pd.DataFrame(data)
    except Exception:
        df = pd.DataFrame()
    for col in ["id", "brand_id", "month", "revenue"]:
        if col not in df.columns:
            df[col] = None
    return df

def load_source():
    """기존 계약시스템(companies / projects / cash_events)은 읽기 전용으로만 사용"""
    try:
        companies = pd.DataFrame(SUPA.table("companies").select("id,name").execute().data)
        projects = pd.DataFrame(SUPA.table("projects").select("*").execute().data)
        cash = pd.DataFrame(SUPA.table("cash_events").select("*").execute().data)
        return companies, projects, cash
    except Exception:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

READY = tables_exist()
brands_df = load_brands()
rev_df = load_revenue()
companies_df, projects_df, cash_df = load_source()

def import_from_contracts():
    """companies 테이블 기준으로, sales_brands에 아직 없는 브랜드를 자동 등록"""
    if companies_df.empty:
        return 0, "계약시스템에 연동된 회사 데이터가 없어요."
    existing_ids = set(brands_df["company_id"].dropna().tolist()) if not brands_df.empty else set()
    inserted = 0
    for _, c in companies_df.iterrows():
        if c["id"] in existing_ids:
            continue
        proj = projects_df[projects_df["company_id"] == c["id"]] if not projects_df.empty else pd.DataFrame()
        budget = int(proj["supply_amount"].fillna(0).sum()) if not proj.empty and "supply_amount" in proj.columns else 0
        plan = f"연동된 프로젝트 {len(proj)}건 (계약 콘솔에서 자동 가져옴)"
        SUPA.table("sales_brands").insert({
            "name": c["name"], "company_id": c["id"], "tier": "일반",
            "monthly_budget": budget, "marketing_plan": plan,
        }).execute()
        inserted += 1
    return inserted, None

def monthly_spend_by_company():
    """cash_events(direction='in': 브랜드가 지불한 금액)를 프로젝트→회사 기준으로 월별 합산"""
    if projects_df.empty or cash_df.empty or "company_id" not in projects_df.columns:
        return pd.DataFrame(columns=["company_id", "month", "amount"])
    proj_to_company = dict(zip(projects_df["id"], projects_df["company_id"]))
    cdf = cash_df.copy()
    cdf["company_id"] = cdf["project_id"].map(proj_to_company)
    cdf = cdf[cdf.get("direction") == "in"]
    if cdf.empty:
        return pd.DataFrame(columns=["company_id", "month", "amount"])
    cdf["due_date"] = pd.to_datetime(cdf["due_date"], errors="coerce")
    cdf["month"] = cdf["due_date"].dt.to_period("M").dt.to_timestamp()
    return cdf.dropna(subset=["month"]).groupby(["company_id", "month"])["amount"].sum().reset_index()

spend_df = monthly_spend_by_company()

st.title("💼 영업 — 마케팅 수주 영업 관리")

if not READY:
    st.warning("아직 필요한 테이블이 없어요. 아래 버튼으로 자동 생성을 시도하거나, 접어둔 SQL로 직접 만들 수 있어요.")
    b1, _ = st.columns([1, 3])
    with b1:
        if st.button("🛠 테이블 자동 생성하기", type="primary"):
            ok, err = create_tables_via_db()
            if ok:
                st.success("테이블을 생성했어요! 새로고침할게요.")
                st.rerun()
            elif err == "missing_url":
                st.error(
                    "자동 생성을 하려면 SUPABASE_DB_URL 환경변수가 필요해요. "
                    "Supabase 대시보드 → Project Settings → Database → Connection string(URI)에서 "
                    "복사해 .env / 호스팅 환경변수에 추가한 뒤 다시 눌러주세요."
                )
            else:
                st.error(f"자동 생성 실패: {err}")
    with st.expander("수동으로 만들고 싶다면 (Supabase SQL Editor)"):
        st.code(TABLE_SQL, language="sql")
        st.caption("이미 sales_brands를 만든 적 있다면 아래 한 줄만 추가로 실행해주세요.")
        st.code(ALTER_SQL, language="sql")
    st.divider()

st.caption("OWM 브랜드 리스트 기반 예산 · 매출 · 마케팅 계획 관리 대시보드 · 계약 콘솔 데이터와 연동")

# ── ① 월별 현황 (최상단: 매출 vs 마케팅비 집행) ───────────────────
st.subheader("월별 현황")
g1, g2 = st.columns(2)
with g1:
    st.markdown("**OWM 매출** _(외부 데이터 · 수동 업로드)_")
    if not rev_df.empty:
        rev_df["month"] = pd.to_datetime(rev_df["month"])
        monthly_rev = rev_df.groupby("month")["revenue"].sum().reset_index().sort_values("month")
        st.line_chart(monthly_rev.set_index("month")["revenue"])
    else:
        st.info("아직 업로드된 매출 데이터가 없어요. '데이터 업로드' 탭에서 올려주세요.")
with g2:
    st.markdown("**마케팅비 집행** _(계약 콘솔 자동 연동)_")
    if not spend_df.empty:
        monthly_spend = spend_df.groupby("month")["amount"].sum().reset_index().sort_values("month")
        st.line_chart(monthly_spend.set_index("month")["amount"])
    else:
        st.info("연동된 계약/입금 데이터가 없어요. 먼저 '기존 프로젝트에서 가져오기'를 눌러주세요.")

st.divider()

# ── ② 대시보드 요약 ─────────────────────────────────────────────
strategic = brands_df[brands_df["tier"] == "전략"] if not brands_df.empty else pd.DataFrame()
general   = brands_df[brands_df["tier"] == "일반"] if not brands_df.empty else pd.DataFrame()

k1, k2, k3, k4 = st.columns(4)
k1.metric("전략브랜드", f"{len(strategic)}개")
k2.metric("일반운영브랜드", f"{len(general)}개")
k3.metric("전체 브랜드 수", f"{len(brands_df)}개")
k4.metric("연동된 프로젝트 수", f"{len(projects_df)}건" if not projects_df.empty else "0건")

st.write("")
c1, c2 = st.columns(2)

with c1:
    st.markdown("**매출 상위 브랜드 (최근월 기준)**")
    if not rev_df.empty and not brands_df.empty:
        latest_month = rev_df["month"].max()
        latest = rev_df[rev_df["month"] == latest_month]
        top = latest.merge(brands_df[["id", "name"]], left_on="brand_id", right_on="id")
        top = top.sort_values("revenue", ascending=False).head(5)
        st.dataframe(
            top[["name", "revenue"]].rename(columns={"name": "브랜드", "revenue": "매출"}),
            hide_index=True, use_container_width=True,
        )
    else:
        st.caption("데이터 없음")

with c2:
    st.markdown("**🚨 급한 미팅 필요 (매출은 나는데 마케팅비는 적은 곳)**")
    if not rev_df.empty and not brands_df.empty:
        latest_month = rev_df["month"].max()
        latest = rev_df[rev_df["month"] == latest_month]
        merged = latest.merge(brands_df, left_on="brand_id", right_on="id", suffixes=("", "_b"))
        if not spend_df.empty:
            spend_latest = spend_df[spend_df["month"] == latest_month][["company_id", "amount"]]
            merged = merged.merge(spend_latest, on="company_id", how="left")
            merged["spend"] = merged["amount"].fillna(0)
        else:
            merged["spend"] = merged["monthly_budget"].fillna(0)
        if len(merged):
            rev_threshold = merged["revenue"].quantile(0.6)
            flagged = merged[
                (merged["revenue"] >= rev_threshold)
                & (merged["spend"] < merged["revenue"] * 0.03)
            ]
            if len(flagged):
                st.dataframe(
                    flagged[["name", "revenue", "spend"]].rename(
                        columns={"name": "브랜드", "revenue": "매출", "spend": "마케팅비 집행"}
                    ),
                    hide_index=True, use_container_width=True,
                )
            else:
                st.caption("해당 없음")
    else:
        st.caption("데이터 없음")

st.divider()

tab1, tab2, tab3, tab4 = st.tabs(["📋 브랜드 관리", "🌳 프로젝트 트리", "📤 데이터 업로드", "🤝 미팅 · 상태체크"])

# ── ③ 브랜드 관리 ────────────────────────────────────────────────
with tab1:
    st.markdown("### 🔄 기존 프로젝트에서 가져오기")
    st.caption("계약 콘솔의 companies 테이블 기준으로, 아직 등록되지 않은 브랜드를 자동으로 채워요. 중복 등록되지 않아요.")
    if st.button("🔄 기존 프로젝트에서 가져오기", type="primary", disabled=not READY):
        n, err = import_from_contracts()
        if err:
            st.warning(err)
        else:
            st.success(f"{n}개 브랜드를 새로 가져왔어요.")
            st.rerun()

    st.markdown("### 브랜드 목록")
    if not brands_df.empty:
        show = brands_df[["name", "tier", "monthly_budget", "status", "satisfaction", "last_meeting_date"]].rename(
            columns={
                "name": "브랜드", "tier": "구분", "monthly_budget": "마케팅예산",
                "status": "상태", "satisfaction": "만족도", "last_meeting_date": "최근미팅일",
            }
        )
        st.dataframe(show, use_container_width=True, hide_index=True)
    else:
        st.caption("등록된 브랜드가 없어요. 위 버튼으로 가져오거나, 아래 폼으로 직접 추가해보세요.")

    with st.expander("➕ 새 브랜드 직접 추가 (계약시스템에 없는 브랜드일 때)"):
        with st.form("add_brand"):
            name = st.text_input("브랜드명")
            tier = st.selectbox("구분", ["전략", "일반"])
            budget = st.number_input("월 마케팅 예산", min_value=0, step=1_000_000)
            plan = st.text_area("마케팅 계획")
            submitted = st.form_submit_button("추가")
            if submitted and name:
                if not READY:
                    st.error("아직 테이블이 없어서 저장할 수 없어요. 위쪽에서 먼저 테이블을 만들어주세요.")
                else:
                    SUPA.table("sales_brands").insert(
                        {"name": name, "tier": tier, "monthly_budget": budget, "marketing_plan": plan}
                    ).execute()
                    st.success(f"{name} 브랜드를 추가했어요.")
                    st.rerun()

# ── ④ 프로젝트 트리 ──────────────────────────────────────────────
with tab2:
    st.markdown("### 브랜드 → 프로젝트 트리")
    st.caption("브랜드(회사) 아래, 계약 콘솔에서 연결된 프로젝트(캠페인/월별 계약)들을 볼 수 있어요.")
    if brands_df.empty or brands_df["company_id"].isna().all():
        st.caption("연동된 브랜드가 없어요. '브랜드 관리' 탭에서 먼저 가져와주세요.")
    else:
        for _, b in brands_df.dropna(subset=["company_id"]).iterrows():
            proj = projects_df[projects_df["company_id"] == b["company_id"]] if not projects_df.empty else pd.DataFrame()
            with st.expander(f"🏢 {b['name']}  ·  프로젝트 {len(proj)}건"):
                if proj.empty:
                    st.caption("연결된 프로젝트가 없어요.")
                else:
                    cols = [c for c in ["product", "campaign", "stage", "supply_amount", "start_date", "end_date"] if c in proj.columns]
                    view = proj[cols].rename(columns={
                        "product": "상품/캠페인", "campaign": "캠페인명", "stage": "단계",
                        "supply_amount": "계약금액", "start_date": "시작일", "end_date": "종료일",
                    })
                    st.dataframe(view, use_container_width=True, hide_index=True)

# ── ⑤ 데이터 업로드 ──────────────────────────────────────────────
with tab3:
    st.markdown("### 월별 OWM 매출 업로드 (CSV)")
    st.caption("컬럼명: 브랜드명, 월(YYYY-MM), 매출 — 세 컬럼을 포함한 CSV를 올려주세요. (마케팅비는 계약 콘솔에서 자동으로 가져와요)")
    st.download_button(
        "📄 업로드 양식(CSV) 받기",
        data="브랜드명,월,매출\ncellimax,2026-07,50000000\n23YEARSOLD,2026-07,32000000\n",
        file_name="영업_매출업로드_양식.csv",
        mime="text/csv",
    )
    file = st.file_uploader("CSV 업로드", type=["csv"])
    if file:
        up = pd.read_csv(file)
        st.dataframe(up.head(), use_container_width=True)
        if st.button("업로드 확정"):
            if not READY:
                st.error("아직 테이블이 없어서 저장할 수 없어요. 위쪽에서 먼저 테이블을 만들어주세요.")
            else:
                name_to_id = dict(zip(brands_df["name"], brands_df["id"])) if not brands_df.empty else {}
                rows, skipped = [], []
                for _, r in up.iterrows():
                    bname = str(r["브랜드명"]).strip()
                    bid = name_to_id.get(bname)
                    if not bid:
                        skipped.append(bname)
                        continue
                    rows.append({
                        "brand_id": bid,
                        "month": pd.to_datetime(str(r["월"])).date().isoformat(),
                        "revenue": float(r["매출"]),
                    })
                if rows:
                    SUPA.table("sales_revenue_monthly").insert(rows).execute()
                    st.success(f"{len(rows)}건 업로드 완료")
                if skipped:
                    st.warning(f"브랜드 관리 탭에 먼저 등록되지 않아 건너뛴 브랜드: {', '.join(set(skipped))}")
                if rows:
                    st.rerun()

# ── ⑥ 미팅 · 상태체크 ────────────────────────────────────────────
with tab4:
    st.markdown("### 미팅 기록 · 상태체크")
    if not brands_df.empty:
        sel = st.selectbox("브랜드 선택", brands_df["name"])
        row = brands_df[brands_df["name"] == sel].iloc[0]
        with st.form("meeting_update"):
            meeting_date = st.date_input(
                "최근 미팅일",
                value=pd.to_datetime(row["last_meeting_date"]).date() if row.get("last_meeting_date") else date.today(),
            )
            notes = st.text_area("미팅 후 메모", value=row.get("meeting_notes") or "")
            status_opts = ["정상", "주의", "이탈위험"]
            satisfaction_opts = ["만족", "보통", "불만족", "미확인"]
            status = st.selectbox("상태", status_opts, index=status_opts.index(row.get("status") or "정상"))
            satisfaction = st.selectbox(
                "만족도", satisfaction_opts, index=satisfaction_opts.index(row.get("satisfaction") or "미확인")
            )
            save = st.form_submit_button("저장")
            if save:
                if not READY:
                    st.error("아직 테이블이 없어서 저장할 수 없어요. 위쪽에서 먼저 테이블을 만들어주세요.")
                else:
                    SUPA.table("sales_brands").update({
                        "last_meeting_date": meeting_date.isoformat(),
                        "meeting_notes": notes,
                        "status": status,
                        "satisfaction": satisfaction,
                    }).eq("id", row["id"]).execute()
                    st.success("업데이트했어요.")
                    st.rerun()
    else:
        st.caption("먼저 '브랜드 관리' 탭에서 브랜드를 등록해주세요.")
