"""직원 입력 현황(COO › Team Input)이 쓰는 계산. streamlit 에 의존하지 않는다(테스트 쉬움).

목적: 직원이 올린 데이터가 화면에 안 보일 때, '시도도 안 한 것'인지 '하다가 멈춘 것'인지 '미완성인 것'인지 구분해서
대표님이 개선 지시를 내릴 수 있게 한다. 숫자를 만들어내지 않는다 — 없으면 '기록 없음'으로 보여준다.
"""
from datetime import date, datetime, timezone

STAGE_LABEL = {
    "loaded": "📥 파일 불러옴",
    "parse_failed": "❌ 파일을 못 읽음",
    "review": "📝 확인 중 (저장 전)",
    "register_blocked": "🚫 등록 눌렀지만 막힘",
    "registered": "✅ 등록 완료",
    "failed": "❌ 저장 중 오류",
}
STUCK_STAGES = {"loaded", "review", "register_blocked"}   # 여기서 멈추면 '끝내지 않은 시도'
FLOW_LABEL = {"done": "✅ 등록 완료", "failed": "❌ 오류로 멈춤", "parse_failed": "❌ 파일을 못 읽음", "stuck": "🟡 끝내지 않음(저장 안 됨)"}


def _dt(ts):
    if not ts:
        return None
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def days_since(ts, now=None):
    d = _dt(ts)
    if not d:
        return None
    return ((now or datetime.now(timezone.utc)) - d).days


def summarize_flows(attempts):
    """시도 기록(한 줄씩)을 '한 번의 작업 흐름'(session_id)으로 묶는다. 최신 흐름이 먼저."""
    by = {}
    for a in sorted(attempts, key=lambda r: str(r.get("created_at"))):
        sid = a.get("session_id") or f"_{a.get('id')}"
        f = by.setdefault(sid, {"session_id": sid, "start": a.get("created_at"), "file": None, "n_rows": None, "n_ok": None,
                                "missing": None, "detail": None, "stages": [], "last_stage": None})
        f["stages"].append(a.get("stage"))
        f["last_stage"] = a.get("stage")
        f["last"] = a.get("created_at")
        if a.get("file_name"):
            f["file"] = a["file_name"]
        if a.get("n_rows") is not None:
            f["n_rows"] = a["n_rows"]
        if a.get("stage") in ("review", "registered", "failed"):
            f["n_ok"] = a.get("n_ok")
        if a.get("stage") == "review":
            f["missing"] = a.get("missing")
        if a.get("detail"):
            f["detail"] = a["detail"]
    out = []
    for f in by.values():
        ls = f["last_stage"]
        f["state"] = "done" if ls == "registered" else "failed" if ls == "failed" else "parse_failed" if ls == "parse_failed" else "stuck"
        out.append(f)
    out.sort(key=lambda f: str(f["last"]), reverse=True)
    return out


def payment_verdict(n_registered, attempts, attempts_available, now=None):
    """송금정보 등록 상태 한 줄 판정. 반환 (등급, 문구) — 등급: ok / warn / bad / unknown"""
    if n_registered:
        base = f"등록 완료 {n_registered}건"
        flows = summarize_flows(attempts)
        stuck = [f for f in flows if f["state"] in ("stuck", "failed", "parse_failed")]
        if stuck and flows and flows[0]["state"] != "done":
            return "warn", base + " · 가장 최근 시도는 끝내지 않았어요"
        return "ok", base
    if not attempts_available:
        return "unknown", "등록된 송금 0건 · 시도 기록 테이블이 아직 없어서 시도 여부를 알 수 없어요 (마이그레이션 적용 필요)"
    if not attempts:
        return "bad", "등록된 송금 0건 · 시도 기록도 없음 (아직 시도하지 않았거나, 기록이 시작되기 전의 일)"
    flows = summarize_flows(attempts)
    last = flows[0]
    d = days_since(last["last"], now)
    when = "오늘" if d == 0 else f"{d}일 전" if d is not None else ""
    what = {"stuck": "불러오거나 확인하다가 저장하지 않고 멈춤", "failed": "저장 중 오류로 멈춤", "parse_failed": "파일을 못 읽어서 멈춤"}.get(last["state"], "")
    return "bad", f"등록된 송금 0건 · 시도 {len(flows)}번 (마지막 {when}: {what})"


def placement_issues(row, today=None):
    """인플루언서 매칭 현황 한 줄의 문제점 목록(문장). 데이터가 없으면 문제로 단정하지 않는 것만 센다."""
    today = today or date.today()
    status = (row.get("status") or "").strip()
    done = "완료" in status
    issues = []
    if done and not (row.get("content_link") or "").strip():
        issues.append("업로드완료인데 콘텐츠 링크 없음")
    if row.get("unit_price") in (None, ""):
        issues.append("단가 없음" + (" (업로드완료)" if done else ""))
    if row.get("guideline_ok") is not True:
        issues.append("가이드라인 확인 안 됨")
    if not done:
        sd = row.get("scheduled_date")
        if not sd:
            issues.append("예정일 없음")
        else:
            try:
                if date.fromisoformat(str(sd)[:10]) < today:
                    issues.append("예정일 지남(아직 업로드 전)")
            except ValueError:
                pass
    return issues


def placement_stats(rows, today=None):
    """{'total', 'by_status', 'counts': {문제: 건수}} — 문제 종류별 건수(앞 괄호 문구는 합쳐서 센다)"""
    by_status, counts = {}, {}
    for r in rows:
        by_status[r.get("status") or "(상태없음)"] = by_status.get(r.get("status") or "(상태없음)", 0) + 1
        for i in placement_issues(r, today):
            k = i.split(" (")[0] if i.startswith("단가 없음") else i
            counts[k] = counts.get(k, 0) + 1
    return {"total": len(rows), "by_status": by_status, "counts": counts}


def build_instruction_items(person, verdict, stats, flows, today=None):
    """개선 지시 문구 후보(항목별). 사람이 고쳐서 쓴다. 담백하고 구체적으로, 감시가 아닌 도움의 말투."""
    lvl, _ = verdict
    items = []
    if lvl in ("bad", "unknown"):
        if flows and flows[0]["state"] == "stuck":
            miss = flows[0].get("missing") or {}
            tail = (" 빠진 항목: " + ", ".join(f"{k} {v}건" for k, v in miss.items()) + ".") if miss else ""
            items.append(f"송금정보: 파일을 불러온 뒤 등록까지 가지 못했어요.{tail} 빠진 항목을 채우고 '일괄 등록'까지 눌러주세요. 막히는 곳이 있으면 알려주세요.")
        elif flows and flows[0]["state"] == "failed":
            items.append(f"송금정보: 저장 중 오류가 났어요({flows[0].get('detail') or '원인 확인 필요'}). 원인을 확인한 뒤 다시 등록해주세요.")
        elif flows and flows[0]["state"] == "parse_failed":
            items.append(f"송금정보: 올린 파일을 읽지 못했어요({flows[0].get('detail') or '원인 확인 필요'}). 표준 양식(CSV·엑셀)으로 다시 올려주세요.")
        elif lvl == "bad":
            items.append("송금정보: 아직 등록된 건이 없어요. 📥 인플루언서 송금정보 등록에서 표준 양식으로 1건이라도 끝까지 등록해주세요.")
    c = stats.get("counts", {})
    if c.get("업로드완료인데 콘텐츠 링크 없음"):
        items.append(f"매칭 현황: 업로드완료인데 콘텐츠 링크가 없는 건이 {c['업로드완료인데 콘텐츠 링크 없음']}건이에요. 링크를 입력해주세요.")
    if c.get("단가 없음"):
        items.append(f"매칭 현황: 단가가 비어 있는 건이 {c['단가 없음']}건이에요. 단가가 있어야 비용·마진 계산에 반영돼요.")
    if c.get("예정일 지남(아직 업로드 전)"):
        items.append(f"매칭 현황: 예정일이 지났는데 업로드 전인 건이 {c['예정일 지남(아직 업로드 전)']}건이에요. 진행 상태를 확인해주세요.")
    if c.get("예정일 없음"):
        items.append(f"매칭 현황: 예정일이 비어 있는 섭외중 건이 {c['예정일 없음']}건이에요. 예정일을 입력해주세요.")
    return items
