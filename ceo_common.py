"""대표 화면(Command Center · Receivables)이 함께 쓰는 계산. streamlit 에 의존하지 않는다(테스트 쉬움).

받을 돈 = cash_events(입금 예정, 아직 paid 아님)에서
  · 은행거래와 매칭된 건            → 이미 받은 것(제외)
  · 대표 확인 '받음'(received)       → 제외
  · 대표 확인 '계약취소'(canceled)   → 제외
  · 대표 확인 '부분입금'(partial)    → 남은 금액(예정 − 지금까지 받은 금액)만 남김
  · 그 외(확인 없음/아직/분쟁)       → 전액 남김
cash_events.paid 는 바꾸지 않는다(재무 완료는 은행거래 매칭으로만). 대표 확인은 ceo_receivable_checks 에 따로 쌓는다.
"""
from datetime import date, datetime, timezone

CHECK_EVERY_DAYS = 14   # 마지막 확인 뒤 이 일수가 지나면 다시 '체크 필요'

STATUS_LABEL = {"received": "받음", "partial": "부분입금", "pending": "아직", "canceled": "계약취소", "disputed": "분쟁/협의"}


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


def latest_check_by_event(checks):
    """이벤트별로 가장 최근(checked_at) 확인 기록 하나."""
    latest = {}
    for c in checks:
        cur = latest.get(c["cash_event_id"])
        if cur is None or str(c["checked_at"]) > str(cur["checked_at"]):
            latest[c["cash_event_id"]] = c
    return latest


def build_receivables(events, projects, matched_ids, checks, today):
    """받을 돈 목록을 (남은 것 open / 정리된 것 closed)로 나눈다. open 은 남은 금액 큰 순.
    events: cash_events 행(direction='in', paid=false) / projects: {id: {brand,...}} / matched_ids: 은행과 매칭된 event id 집합."""
    latest = latest_check_by_event(checks)
    open_rows, closed_rows = [], []
    for e in events:
        amount = float(e.get("amount") or 0)
        pr = projects.get(e.get("project_id")) or {}
        ck = latest.get(e["id"])
        due = to_date(e.get("due_date"))
        row = {
            "id": e["id"], "brand": pr.get("brand") or "", "title": e.get("title") or "", "amount": amount,
            "due_date": due, "overdue_days": (today - due).days if due and due < today else 0,
            "check": ck, "received": 0.0, "remaining": amount, "state": "open", "need_check": False,
        }
        if e["id"] in matched_ids:
            row.update(state="matched", remaining=0.0, received=amount)
        elif ck and ck["status"] == "received":
            row.update(state="received", remaining=0.0, received=amount)
        elif ck and ck["status"] == "canceled":
            row.update(state="canceled", remaining=0.0)
        elif ck and ck["status"] == "partial":
            got = min(float(ck.get("received_amount") or 0), amount)
            row.update(received=got, remaining=max(amount - got, 0.0))
            if row["remaining"] <= 0:
                row["state"] = "received"
        if row["state"] == "open":
            age = days_ago(ck["checked_at"]) if ck else None
            row["need_check"] = ck is None or (age or 0) >= CHECK_EVERY_DAYS
            open_rows.append(row)
        else:
            closed_rows.append(row)
    open_rows.sort(key=lambda r: -r["remaining"])
    return {"open": open_rows, "closed": closed_rows}


def summarize(open_rows):
    need = [r for r in open_rows if r["need_check"]]
    late = [r for r in open_rows if r["overdue_days"] > 0]
    return {
        "total": sum(r["remaining"] for r in open_rows), "n": len(open_rows),
        "need_n": len(need), "need_sum": sum(r["remaining"] for r in need),
        "late_n": len(late), "late_sum": sum(r["remaining"] for r in late),
        "max_over": max([r["overdue_days"] for r in late], default=0),
        "partial_got": sum(r["received"] for r in open_rows),
    }
