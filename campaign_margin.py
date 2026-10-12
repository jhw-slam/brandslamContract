"""캠페인 마진율 계산 (CFO › Campaign Margin). streamlit 에 의존하지 않는다(테스트 쉬움).

데이터 출처 — 우리는 직원이 쌓은 데이터를 '가져와서 표현'만 한다.
  캠페인·계약서·인보이스   김선재 → sales_campaigns (+ sales_accounts 브랜드명)
  콘텐츠(인플루언서 송금)  곽재선 → payment_requests, 캠페인 연결 payment_request_campaigns(allocated_amount)
  입금 확인               김선재 → campaign_deposit_links (은행 입금 1건 ↔ 캠페인 N개)
  목표 콘텐츠 개수·마진율  campaign_margin_settings

규칙
  · 매출 = 인보이스 '공급가'(부가세 제외). 부가세 구분이 '포함'이면 ÷1.1, '별도'/'해당없음'이면 그대로.
  · 비용 = 이 캠페인에 배분된 금액. 한 콘텐츠가 여러 캠페인에 걸리면(PPL) 이미 1/n(또는 대표가 정한 비율)로 나뉘어 저장돼 있다.
  · 원화(KRW)만 합산한다. 다른 통화는 환율을 추측하지 않고 '환산 필요'로만 표시한다.
  · 입금은 공급가+부가세로 들어오므로 현금 기준 매출 = 입금 ÷ 1.1 (부가세 구분을 모르면 계산하지 않고 '확인 필요').
  · 목표 마진율 65% = 매출이 100일 때 콘텐츠 총 원가는 35%(원가 한도).
"""
from collections import Counter
from datetime import date, datetime

DEFAULT_TARGET_MARGIN = 0.65
VAT_RATE = 0.10
NEAR_LIMIT = 0.9   # 원가 한도의 90% 넘으면 '한도 근접'


def to_date(v):
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return date.fromisoformat(str(v)[:10])


def is_krw(cur):
    return (cur or "KRW").strip().upper() in ("KRW", "원", "₩", "")


def vat_kind(vat_type):
    """인보이스의 부가세 구분 → included(포함) / excluded(별도) / none(해당없음) / None(미확인)."""
    t = (vat_type or "").strip()
    if t.startswith("포함"):
        return "included"
    if t.startswith("별도"):
        return "excluded"
    if t.startswith("해당없음"):
        return "none"
    return None


def supply_from_invoice(amount, vat_type):
    """인보이스 금액 → 공급가(부가세 제외). 구분이 없으면 금액 그대로(화면에 확인 필요 표시)."""
    if amount is None:
        return None
    return amount / (1 + VAT_RATE) if vat_kind(vat_type) == "included" else amount


def supply_from_deposit(gross, vat_type):
    """입금액(부가세 포함해서 들어옴) → 공급가. 구분을 모르면 None(추측하지 않음)."""
    k = vat_kind(vat_type)
    if k in ("included", "excluded"):
        return gross / (1 + VAT_RATE)
    if k == "none":
        return gross
    return None


def margin_status(revenue, cost, target_margin):
    """원가 한도(매출 × (1−목표 마진율)) 대비 신호. (코드, 라벨, 한도, 사용률)."""
    if revenue is None or revenue <= 0:
        return "wait", "매출 대기", None, None
    limit = revenue * (1 - target_margin)
    usage = cost / limit if limit > 0 else None
    if cost > limit + 1e-6:
        return "over", "원가 초과(과잉)", limit, usage
    if usage is not None and usage >= NEAR_LIMIT:
        return "near", "한도 근접", limit, usage
    return "ok", "여유", limit, usage


def count_status(actual, target):
    """콘텐츠 개수 신호. (코드, 라벨)."""
    if target is None:
        return "no_target", "목표 미입력"
    if actual >= target:
        return "done", "달성" if actual == target else f"초과 +{actual - target}"
    return "short", f"부족 {target - actual}개"


def build_campaigns(campaigns, accounts, links, requests, deposits, banks, settings, default_target=DEFAULT_TARGET_MARGIN):
    """캠페인별 한 줄 요약을 만든다.
    campaigns: sales_campaigns 행 / accounts: {id: sales_accounts 행} / links: payment_request_campaigns 행
    requests: {id: payment_requests 행} / deposits: campaign_deposit_links 행 / banks: {id: bank_transactions 행}
    settings: {campaign_id: campaign_margin_settings 행}"""
    n_by_req = Counter(l["payment_request_id"] for l in links)
    links_by_c, deps_by_c = {}, {}
    for l in links:
        links_by_c.setdefault(l["campaign_id"], []).append(l)
    for d in deposits:
        deps_by_c.setdefault(d["campaign_id"], []).append(d)

    out = []
    for c in campaigns:
        cid = c["id"]
        st = settings.get(cid) or {}
        target_margin = float(st["target_margin"]) if st.get("target_margin") is not None else default_target
        brand = (accounts.get(c.get("account_id")) or {}).get("brand_name") or ""

        # ── 매출(인보이스) ──
        inv_amount = float(c["invoice_amount"]) if c.get("invoice_amount") is not None else None
        inv_krw = is_krw(c.get("invoice_currency"))
        vkind = vat_kind(c.get("invoice_vat_type"))
        revenue = supply_from_invoice(inv_amount, c.get("invoice_vat_type")) if (inv_amount is not None and inv_krw) else None

        # ── 콘텐츠·비용 ──
        contents, cost, cost_paid, cost_planned, foreign = [], 0.0, 0.0, 0.0, {}
        for l in links_by_c.get(cid, []):
            r = requests.get(l["payment_request_id"]) or {}
            cur = l.get("currency") or r.get("currency")
            alloc = float(l.get("allocated_amount") or 0)
            paid = (r.get("status") == "paid") or bool(r.get("paid_at"))
            item = {
                "request_id": l["payment_request_id"], "who": r.get("influencer_name") or "(삭제된 송금)", "allocated": alloc,
                "currency": (cur or "KRW"), "krw": is_krw(cur), "paid": paid,
                "paid_date": to_date(r.get("paid_at")), "scheduled": to_date(r.get("scheduled_date")),
                "link": r.get("content_link") or "", "n_campaigns": n_by_req[l["payment_request_id"]],
                "shared": n_by_req[l["payment_request_id"]] > 1, "ratio": l.get("ratio"),
            }
            contents.append(item)
            if item["krw"]:
                cost += alloc
                if paid:
                    cost_paid += alloc
                else:
                    cost_planned += alloc
            else:
                foreign[item["currency"]] = foreign.get(item["currency"], 0.0) + alloc

        # ── 입금(확정된 것만) ──
        dep_rows = []
        for d in deps_by_c.get(cid, []):
            b = banks.get(d["bank_transaction_id"]) or {}
            dep_rows.append({"date": to_date(b.get("txn_date")), "gross": float(d.get("allocated_amount") or 0),
                             "who": b.get("description") or "", "bank_id": d["bank_transaction_id"]})
        dep_gross = sum(x["gross"] for x in dep_rows)
        cash_revenue = supply_from_deposit(dep_gross, c.get("invoice_vat_type")) if dep_rows else (0.0 if vkind else None)

        code, label, limit, usage = margin_status(revenue, cost, target_margin)
        target_n = st.get("target_content_count")
        n_code, n_label = count_status(len(contents), target_n)
        remaining_limit = (limit - cost) if limit is not None else None
        remaining_n = max(target_n - len(contents), 0) if target_n is not None else None
        per_content_budget = (remaining_limit / remaining_n) if (remaining_limit is not None and remaining_n) else None

        out.append({
            "id": cid, "brand": brand, "name": c.get("campaign_name") or "", "open_date": to_date(c.get("open_date")),
            "status": c.get("status") or "", "contract": bool(c.get("contract_url")), "contract_url": c.get("contract_url") or "",
            "invoice_no": c.get("invoice_number") or "", "invoice_amount": inv_amount, "invoice_currency": (c.get("invoice_currency") or "KRW"),
            "invoice_krw": inv_krw, "invoice_vat_type": c.get("invoice_vat_type") or "", "vat_kind": vkind,
            "invoice_issue_date": to_date(c.get("invoice_issue_date")), "invoice_url": c.get("invoice_url") or "",
            "revenue": revenue, "vat_unknown": inv_amount is not None and vkind is None,
            "contents": contents, "n_contents": len(contents), "n_shared": sum(1 for x in contents if x["shared"]),
            "target_n": target_n, "count_code": n_code, "count_label": n_label,
            "cost": cost, "cost_paid": cost_paid, "cost_planned": cost_planned, "foreign": foreign,
            "margin": (revenue - cost) if revenue is not None else None,
            "margin_rate": ((revenue - cost) / revenue) if revenue else None,
            "target_margin": target_margin, "limit": limit, "usage": usage, "m_code": code, "m_label": label,
            "remaining_limit": remaining_limit, "remaining_n": remaining_n, "per_content_budget": per_content_budget,
            "deposits": dep_rows, "deposit_gross": dep_gross, "cash_revenue": cash_revenue,
        })
    return out


def cash_events(rows):
    """현금흐름 시점 목록: 입금(확정된 입금 날짜) / 출금(지급일, 없으면 예정일). 날짜 없는 것은 맨 뒤."""
    ev = []
    for r in rows:
        label = f"{r['brand']} {r['name']}".strip()
        for d in r["deposits"]:
            ev.append({"date": d["date"], "kind": "입금", "state": "확정", "amount": d["gross"], "campaign": label, "memo": d["who"]})
        for c in r["contents"]:
            if not c["krw"]:
                continue
            when = c["paid_date"] if c["paid"] and c["paid_date"] else c["scheduled"]
            ev.append({"date": when, "kind": "출금", "state": "지급완료" if c["paid"] else "예정", "amount": c["allocated"], "campaign": label, "memo": c["who"]})
    ev.sort(key=lambda e: (e["date"] is None, e["date"] or date.max, e["kind"]))
    return ev


def monthly_cashflow(events):
    """월별 입금 / 출금(지급완료) / 출금(예정) / 순현금(입금−출금 전체). 날짜 없는 건은 '일정 미정'."""
    months = {}
    for e in events:
        key = e["date"].strftime("%Y-%m") if e["date"] else "일정 미정"
        m = months.setdefault(key, {"month": key, "in": 0.0, "out_paid": 0.0, "out_planned": 0.0})
        if e["kind"] == "입금":
            m["in"] += e["amount"]
        elif e["state"] == "지급완료":
            m["out_paid"] += e["amount"]
        else:
            m["out_planned"] += e["amount"]
    rows = sorted(months.values(), key=lambda m: (m["month"] == "일정 미정", m["month"]))
    run = 0.0
    for m in rows:
        m["net"] = m["in"] - m["out_paid"] - m["out_planned"]
        run += m["net"]
        m["cum"] = run
    return rows


def summarize(rows):
    rev = [r for r in rows if r["revenue"] is not None]
    revenue = sum(r["revenue"] for r in rev)
    cost_in_rev = sum(r["cost"] for r in rev)           # 마진율은 '매출이 있는' 캠페인끼리만 비교
    return {
        "n": len(rows), "n_rev": len(rev), "revenue": revenue, "cost": sum(r["cost"] for r in rows), "cost_in_rev": cost_in_rev,
        "margin_rate": ((revenue - cost_in_rev) / revenue) if revenue else None,
        "n_contents": sum(r["n_contents"] for r in rows), "target_n": sum(r["target_n"] or 0 for r in rows),
        "n_target_set": sum(1 for r in rows if r["target_n"] is not None),
        "deposit_gross": sum(r["deposit_gross"] for r in rows),
        "cost_paid": sum(r["cost_paid"] for r in rows), "cost_planned": sum(r["cost_planned"] for r in rows),
        "n_over": sum(1 for r in rows if r["m_code"] == "over"), "n_near": sum(1 for r in rows if r["m_code"] == "near"),
        "n_wait": sum(1 for r in rows if r["m_code"] == "wait"), "foreign": sum(len(r["foreign"]) for r in rows),
    }
