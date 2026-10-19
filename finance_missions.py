"""재무 미션(대표님이 시간 날 때 빠르게 처리하는 일) 계산. streamlit 에 의존하지 않는다(테스트 쉬움).

원칙(10/19 대표 요청): 미확정이어도 숫자는 다 반영하되, 확정은 '표 한 장에서 20개씩 · 기본값은 이미 채워 둠 · 특이사항만 고쳐서 일괄처리'로 빠르게.
"""
import pandas as pd

from expense_breakdown import normalize_merchant, suggest_category

PAGE = 20          # 한 화면에 보여주는 줄 수
NONE_LABEL = "(선택 안함)"


def pending_pairs(bank_df):
    """자동으로 찾았지만 아직 일괄처리 전인 상쇄 쌍. 한 쌍 = 한 줄. 최근 날짜순."""
    if bank_df.empty:
        return []
    d = bank_df[bank_df["is_dup_certain"] & ~bank_df["offset_confirmed"] & bank_df["offset_pair"].notna()]
    out = []
    for key, g in d.groupby("offset_pair"):
        if len(g) != 2:
            continue
        o = g[g["direction"] == "out"]
        i = g[g["direction"] == "in"]
        if o.empty or i.empty:
            continue
        o, i = o.iloc[0], i.iloc[0]
        tm = lambda r: r["txn_datetime"].strftime("%H:%M:%S") if pd.notna(r.get("txn_datetime")) else "-"
        out.append({"key": key, "ids": [o["id"], i["id"]], "date": o["txn_date"], "account": o["account_label"], "amount": float(o["amount"]),
                    "out_time": tm(o), "in_time": tm(i), "gap_min": o.get("pair_gap_min"),
                    "out_desc": o.get("description") or "", "in_desc": i.get("description") or ""})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def suspect_groups(bank_df):
    """같은 날·같은 금액의 입금/출금인데 짝을 못 지은 '애매한 의심' 묶음. 한 묶음 = 한 줄."""
    if bank_df.empty:
        return []
    d = bank_df[bank_df["is_dup_suspect"]]
    out = []
    for key, g in d.groupby("dup_group"):
        o, i = g[g["direction"] == "out"], g[g["direction"] == "in"]
        if o.empty or i.empty:
            continue
        tm = lambda sub: ", ".join(r["txn_datetime"].strftime("%H:%M") if pd.notna(r.get("txn_datetime")) else "-" for _, r in sub.iterrows())
        out.append({"key": key, "ids": g["id"].tolist(), "date": g["txn_date"].iloc[0], "account": g["account_label"].iloc[0], "amount": float(g["amount"].iloc[0]),
                    "out_time": tm(o), "in_time": tm(i), "desc": (o["description"].iloc[0] or "") + " / " + (i["description"].iloc[0] or "")})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out


def merchant_groups(bank_df, name_by_code):
    """분류가 필요한 거래(미분류 + 소액 자동분류 중 대표님이 아직 확정 안 한 것)를 거래처별로 묶는다. 합계 큰 순.
    제안(키워드)은 지출·내부이체에만 붙이고, 확신도 '높음'만 기본값으로 채운다(나머지는 참고만)."""
    if bank_df.empty:
        return []
    auto = bank_df["is_auto_categorized"].fillna(False).astype(bool) if "is_auto_categorized" in bank_df.columns else pd.Series(False, index=bank_df.index)
    elig = bank_df[~bank_df["is_dup_certain"] & (bank_df["account_category_id"].isna() | auto)]
    by = {}
    for _, r in elig.iterrows():
        k = (r["direction"], normalize_merchant(r["description"]))
        g = by.setdefault(k, {"merchant": k[1], "direction": k[0], "n": 0, "total": 0.0, "ids": [], "cur": set()})
        g["n"] += 1; g["total"] += float(r["amount"]); g["ids"].append(r["id"])
        g["cur"].add("미분류" if pd.isna(r["account_category_id"]) else str(r.get("cat_name") or "자동분류"))
    out = []
    for g in by.values():
        sug = suggest_category(g["merchant"])
        code, conf, why = (sug if sug and (g["direction"] == "out" or sug[0] == "TRANSFER_INTERNAL") else (None, None, "근거 부족 — 직접 선택"))
        name = name_by_code.get(code) if code else None
        g.update({"cur": " / ".join(sorted(g["cur"])), "code": code, "conf": conf, "why": why, "sug_name": name,
                  "apply_default": bool(name and conf == "high")})
        out.append(g)
    out.sort(key=lambda g: -g["total"])
    return out


def compute_missions(bank_df, name_by_code, tax_unc=0, ev_unc=0):
    """미션 카드 목록. 각 항목: id, icon, title, count(처리할 줄/쌍/거래처 수), rows(관련 거래 건수), amount, hint"""
    ms = []
    pairs = pending_pairs(bank_df)
    if pairs:
        ms.append({"id": "offset", "icon": "🔁", "title": "상쇄 확인", "count": len(pairs), "rows": len(pairs) * 2, "amount": sum(p["amount"] for p in pairs),
                   "hint": "입금↔출금 쌍 — 기본으로 상쇄 체크돼 있어요. 특이한 것만 체크 풀고 일괄처리"})
    sus = suspect_groups(bank_df)
    if sus:
        ms.append({"id": "suspect", "icon": "⚠️", "title": "애매한 의심 확인", "count": len(sus), "rows": sum(len(s["ids"]) for s in sus), "amount": sum(s["amount"] for s in sus) * 1.0,
                   "hint": "같은 날·같은 금액의 입금/출금 — 기본은 '정상'. 상쇄인 것만 체크"})
    mg = merchant_groups(bank_df, name_by_code)
    if mg:
        ms.append({"id": "classify", "icon": "🏷️", "title": "계정과목 설정 필요", "count": len(mg), "rows": sum(g["n"] for g in mg), "amount": sum(g["total"] for g in mg),
                   "hint": "거래처별 묶음 — 확신 높은 제안은 미리 채워 둠. 거래처 하나 고르면 그 거래처 전체 거래에 적용"})
    if tax_unc:
        ms.append({"id": "tax", "icon": "🧾", "title": "세무항목 설정 필요 (세금계산서)", "count": tax_unc, "rows": tax_unc, "amount": None,
                   "hint": "세금계산서의 계정과목 — 표에서 한꺼번에 선택"})
    if ev_unc:
        ms.append({"id": "event", "icon": "📅", "title": "송금일정 계정과목", "count": ev_unc, "rows": ev_unc, "amount": None, "hint": "전체 매칭 현황 › 송금일정에서 분류"})
    return ms


def fetch_all(SUPA, table, cols, order=None, page=1000):
    """PostgREST 1000행 제한을 넘겨 끝까지 읽는다."""
    out, start = [], 0
    while True:
        q = SUPA.table(table).select(cols)
        if order:
            q = q.order(order).order("id")
        rows = q.range(start, start + page - 1).execute().data
        out += rows
        if len(rows) < page:
            return out
        start += page


def build_state(SUPA):
    """대표 화면(Command Center 등)이 쓰는 재무 미션 요약. 반환 {"missions": [...], "pct_amount", "pct_n", "pending_n", "total_n", "error"}.
    읽지 못하면 error 문구만 채워 돌려준다(화면이 이유를 보여주고 죽지 않게)."""
    from bank_common import add_confirm_state, apply_internal_rule, mark_duplicates, parse_txn_datetime
    try:
        cats = SUPA.table("fin_account_categories").select("id,code,name,type").execute().data
        rows = fetch_all(SUPA, "bank_transactions",
                         "id,direction,amount,txn_date,txn_datetime,description,account_label,account_category_id,is_auto_categorized", "txn_date")
    except Exception as e:
        return {"missions": [], "error": f"{type(e).__name__}: {str(e)[:120]}"}
    keep, off = set(), set()
    try:
        for r in SUPA.table("bank_offset_overrides").select("bank_transaction_id,decision").execute().data:
            (keep if r.get("decision") == "keep" else off).add(r["bank_transaction_id"])
    except Exception:
        pass   # 테이블이 없으면 자동 판단만
    cat_by_id = {c["id"]: c for c in cats}
    df = pd.DataFrame(rows)
    if not df.empty:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
        df["txn_date"] = pd.to_datetime(df["txn_date"], errors="coerce")
        df["txn_datetime"] = parse_txn_datetime(df["txn_datetime"])
        df["cat_name"] = df["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("name", "미분류"))
        df = add_confirm_state(mark_duplicates(apply_internal_rule(df, cats), keep, off))
    tax_unc = ev_unc = 0
    try:
        tax_unc = SUPA.table("tax_invoices").select("approval_no", count="exact").is_("account_category_id", "null").eq("canceled", False).execute().count or 0
    except Exception:
        pass
    try:
        ev_unc = SUPA.table("cash_events").select("id", count="exact").is_("account_category_id", "null").execute().count or 0
    except Exception:
        pass
    ms = compute_missions(df, {c["code"]: c["name"] for c in cats}, tax_unc, ev_unc) if not df.empty else []
    if df.empty:
        return {"missions": ms, "pct_amount": 0, "pct_n": 0, "pending_n": 0, "total_n": 0, "error": None}
    uni = df[~(df["is_dup_certain"] & df["offset_confirmed"])]
    conf = uni[uni["confirm_state"] == "확정"]
    return {"missions": ms, "pct_amount": float(conf["amount"].sum() / max(uni["amount"].sum(), 1)), "pct_n": len(conf) / max(len(uni), 1),
            "pending_n": len(uni) - len(conf), "total_n": len(uni), "error": None}
