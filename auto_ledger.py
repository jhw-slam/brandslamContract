"""재무 '자동 정리' 엔진 (10/27 대표 요청). streamlit 에 의존하지 않는다(테스트 쉬움). 설계: docs/finance_auto_ledger_plan.md

목표: 은행거래 전부를 사람 손 없이 '약식으로 100%' 정리해 숫자를 먼저 보여준다. 대표님은 나중에 특이한 것만 고친다.
· DB는 바꾸지 않는다(화면 계산용). 대표님이 저장한 분류(is_auto_categorized=False)는 절대 덮어쓰지 않는다.
· 확신이 낮은 추측은 숫자를 왜곡하지 않도록 '분류 대기'에 둔다(제안만 표시). 숫자에는 대기도 별도 줄로 들어간다.
· 급여·인건비는 계정과목 합계만 다룬다(개인별 내역은 화면에서 가린다).

분류 순서(먼저 맞는 것):
  ① 대표 확정  ② 내부이체(짝 확인)  ③ 상쇄(자동 제외)  ④ 같은 거래처 이력  ⑤ 키워드 규칙  ⑥ 기존 자동분류 유지  ⑦ 분류 대기
"""
import re
import unicodedata

import pandas as pd

from bank_common import apply_internal_rule, mark_duplicates
from expense_breakdown import RULES as BASE_RULES

CATCH_ALL = "SGA_MISC_SMALL"      # DB 트리거가 20만 원 미만 출금에 붙이는 임시 분류 — 더 세밀하게 다시 본다
INTERNAL_CODE = "TRANSFER_INTERNAL"
OFFSET_DAYS = 7                   # 카드 취소·환불은 최대 1주일까지 걸린다(대표 10/27)
CARD_LIKE = re.compile(r"카드|현대|삼성|신한|국민|롯데|비씨|BC|하나|우리|농협|페이|PAY|PG|이니시스|KG|나이스|NICE|토스|환불|취소|CANCEL|REFUND", re.I)

BANK_PREFIX = re.compile(r"^(기업|우리|신한|국민|농협|하나|SC|제일|카카오|토스|새마을|수협|부산|대구|경남|광주|전북|제주|우체국|IBK|KB|NH)(?=[\s\-_(]|주식회사)[\s\-_]*")
CORP_MARK = re.compile(r"\(주\)|㈜|주식회사|\(유\)|유한회사|\(사\)|\(재\)")

# (정규식, 방향 'in'/'out'/None, 계정코드, 확신도, 근거). 회사 규칙(expense_breakdown.RULES)보다 먼저 본다.
ENGINE_RULES = [
    (r"급여\d*$|^급여|월급|상여금", "out", "LABOR_SALARY", "high", "급여 이체"),
    (r"국민건강|건강보험|국민연금|고용보험|산재보험|4대보험|근로복지공단", "out", "LABOR_4INSURANCE", "high", "4대보험"),
    (r"임대료|임차료|월세|사무실임대", "out", "SGA_RENT", "high", "사무실 임대료"),
    (r"부가세", "out", "TAX_VAT_PAYABLE", "medium", "부가세 납부"),
    (r"근로소득세|원천세|원천징수", "out", "TAX_WITHHOLDING", "high", "원천세 납부"),
    (r"이자", "in", "OTHER_INTEREST_INCOME", "medium", "이자 수입"),
    (r"캐시백|체크취소|출금취소|가승인|환불", "in", "OTHER_MISC", "medium", "취소·환불·캐시백 입금(짝이 되는 결제가 없을 때)"),
]


def norm_text(desc):
    return unicodedata.normalize("NFKC", str(desc or "")).strip()


CHANNEL = re.compile(r"\s(인터넷|타행이체|체크|RTC|RTI|IB|FBS|BC)(\s|$).*$")
LEAD_DATE = re.compile(r"^\d{4,6}(?=\D)")


def clean_key(desc):
    """거래처 묶음 키. 은행이 붙이는 은행 이름·창구 꼬리(인터넷/체크/RTC…), (주)/주식회사, 띄어쓰기·특수문자, 앞의 날짜, 끝의 긴 숫자를 지운다.
    예) '기업－（주）브랜드슬' → '브랜드슬', '우리 주식회사퍼스트' → '퍼스트', '2609국민건강 건강 (여의도중부)' → '국민건강',
        'PAYPAL *PIXIE DUST 4029357733 MYS' → 'PAYPAL*PIXIE'(페이팔은 사람 이름까지만 본다)"""
    s = norm_text(desc).upper()
    m = re.match(r"PAYPAL\s*\*\s*(\S+)", s)
    if m:
        return "PAYPAL*" + m.group(1)
    s = re.sub(r"INV#\S*", "", s)
    s = CHANNEL.sub("", s)
    t = BANK_PREFIX.sub("", s, count=1)
    if len(t.strip()) >= 2:
        s = t
    s = re.sub(r"\s*\([^)]{2,}\)?\s*$", "", s)      # 끝의 '(여의도중부)' 같은 지점 표시
    s = CORP_MARK.sub("", s)
    s = LEAD_DATE.sub("", s.strip())
    s = re.sub(r"[\s\-_*#.,/()&＆]+", "", s)
    s = re.sub(r"\d{6,}$", "", s)
    return s


# ── 상쇄 ──────────────────────────────────────────────────────

def _evidence(out_desc, in_desc):
    """7일 안의 입금·출금 쌍을 '카드 취소·환불'로 볼 근거가 있는지. (근거 없으면 '의심'으로만 둔다)"""
    a, b = norm_text(out_desc), norm_text(in_desc)
    if CARD_LIKE.search(b) or CARD_LIKE.search(a):
        return "카드·PG·환불 성격의 적요"
    ka, kb = clean_key(a), clean_key(b)
    if len(ka) >= 3 and len(kb) >= 3 and (ka in kb or kb in ka):
        return "같은 거래처"
    return ""


def find_offsets(df, skip_ids=None):
    """같은 계좌·같은 금액·반대 방향 쌍을 7일 안에서 가장 가까운 것끼리 1:1로 짝짓는다.
    반환 DataFrame 에 offset_tier('' | '카드취소' | '의심'), offset_pair_id, offset_gap_days 를 더한다. skip_ids 는 짝짓기에서 뺀다."""
    d = df.copy()
    n = len(d)
    tier, pair_id, gaps = [""] * n, [None] * n, [None] * n
    skip_ids = skip_ids or set()
    ids = d["id"].tolist()
    dirs = d["direction"].tolist()
    amts = d["amount"].tolist()
    accts = d["account_label"].tolist()
    descs = d["description"].tolist()
    days = pd.to_datetime(d["txn_date"], errors="coerce").tolist()
    tms = pd.to_datetime(d["txn_datetime"], errors="coerce").tolist() if "txn_datetime" in d.columns else [pd.NaT] * n
    groups = {}
    for pos in range(n):
        if ids[pos] in skip_ids or pd.isna(days[pos]) or not (amts[pos] > 0):
            continue
        groups.setdefault((accts[pos], amts[pos]), ([], []))[0 if dirs[pos] == "out" else 1].append(pos)
    for (_acct, _amt), (outs, ins) in groups.items():
        if not outs or not ins:
            continue
        cands = []
        for o in outs:
            for i in ins:
                gap = abs((days[i] - days[o]).days)
                if gap > OFFSET_DAYS:
                    continue
                tgap = abs((tms[i] - tms[o]).total_seconds()) if (pd.notna(tms[o]) and pd.notna(tms[i])) else 0.0
                cands.append((gap, tgap, o, i))
        used = set()
        for gap, _tgap, o, i in sorted(cands, key=lambda x: (x[0], x[1])):
            if o in used or i in used:
                continue
            used.update((o, i))
            t = "카드취소" if _evidence(descs[o], descs[i]) else "의심"
            key = f"{ids[o]}|{ids[i]}"
            for pos in (o, i):
                tier[pos], pair_id[pos], gaps[pos] = t, key, gap
    d["offset_tier"], d["offset_pair_id"], d["offset_gap_days"] = tier, pair_id, gaps
    return d


# ── 이력 학습 ─────────────────────────────────────────────────

def build_history(rows):
    """rows: DataFrame(direction, desc 키 ledger_key, account_category_id) — 대표님이 확정한 거래. 반환 {(방향,키): {분류id: 건수}}"""
    hist = {}
    for r in rows.itertuples():
        hist.setdefault((r.direction, r.ledger_key), {}).setdefault(r.account_category_id, 0)
        hist[(r.direction, r.ledger_key)][r.account_category_id] += 1
    return hist


def _pick(cands, cat_type):
    """여러 분류로 갈린 거래처에서 하나를 고른다. 같은 계열(예: 시딩형/방문형 모두 매출)이면 가장 많은 분류, 아니면 90% 이상 한쪽일 때만."""
    tot = sum(cands.values())
    top, n = max(cands.items(), key=lambda kv: kv[1])
    types = {cat_type(c) for c in cands}
    if len(types) == 1:
        return top, "medium", f"같은 거래처가 비슷한 분류로 갈림 — 가장 많은 분류 적용({n}/{tot}건)"
    if tot >= 5 and n / tot >= 0.9:
        return top, "medium", f"같은 거래처를 대표님이 대부분({n}/{tot}건) 이 분류로 정함"
    return None


def history_lookup(hist, direction, key, cat_type=lambda c: None):
    """(분류id, 확신도, 근거) 또는 None. 같은 거래처가 서로 다른 계열로 갈렸으면 따르지 않는다."""
    if len(key) < 2:
        return None
    got = hist.get((direction, key))
    if got:
        if len(got) == 1:
            cid, n = next(iter(got.items()))
            return cid, ("high" if n >= 2 else "medium"), f"같은 거래처를 대표님이 {n}번 분류함"
        return _pick(got, cat_type)
    if len(key) >= 4:     # 은행이 이름을 앞부분만 줄 때(길이 4 이상 + 결과가 하나로 모일 때만. '마이다스'와 '마이다스엠' 같은 다른 회사 주의)
        found = {}
        for (dr, hk), cats in hist.items():
            if dr == direction and len(hk) >= 4 and (hk.startswith(key) or key.startswith(hk)):
                for cid, n in cats.items():
                    found[cid] = found.get(cid, 0) + n
        if len(found) == 1:
            cid, n = next(iter(found.items()))
            return cid, "medium", f"비슷한 이름의 거래처를 대표님이 {n}번 분류함"
        if found:
            return _pick(found, cat_type)
    return None


# ── 키워드 규칙 ───────────────────────────────────────────────

def rule_lookup(desc, direction):
    """(코드, 확신도, 근거) 또는 None. 엔진 규칙 → 회사 규칙 순."""
    s = norm_text(desc).upper()
    for pat, dr, code, conf, why in ENGINE_RULES:
        if (dr is None or dr == direction) and re.search(pat.upper(), s):
            return code, conf, why
    for pat, code, conf, why in BASE_RULES:
        if code != INTERNAL_CODE and re.search(pat.upper(), s):   # 내부이체는 반대편 거래(짝)로만 판단한다
            return code, conf, why
    return None


COST_TYPES = {"cost_cogs", "cost_sga", "labor"}


def _compatible(direction, ctype):
    """입금에 비용 계정, 출금에 매출 계정을 붙이지 않는다(환불은 상쇄로 처리)."""
    if direction == "in":
        return ctype not in COST_TYPES
    return ctype != "revenue"


def prepare_bank_df(rows, parse_dt=None):
    """DB 에서 읽은 은행거래(dict 목록)를 엔진이 쓰는 DataFrame 으로 바꾼다(금액·날짜 정리)."""
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    for c, v in (("description", ""), ("account_label", ""), ("account_category_id", None), ("is_auto_categorized", False)):
        if c not in d.columns:     # 열이 없는 DB(옛 버전)에서도 죽지 않게
            d[c] = v
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce").fillna(0)
    d["txn_date"] = pd.to_datetime(d["txn_date"], errors="coerce")
    if "txn_datetime" in d.columns and parse_dt:
        d["txn_datetime"] = parse_dt(d["txn_datetime"])
    elif "txn_datetime" not in d.columns:
        d["txn_datetime"] = pd.NaT
    return d


# ── 전체 분류 ─────────────────────────────────────────────────

def classify_ledger(df, cats, keep_ids=None, offset_ids=None, later_ids=None):
    """은행거래 DataFrame 을 받아 자동 정리 열을 붙여 돌려준다.
    필요한 열: id, direction, amount, txn_date, txn_datetime, description, account_label, account_category_id, is_auto_categorized
    추가되는 열:
      ledger_cat_id / ledger_code / ledger_cat_name / ledger_type  — 숫자에 쓰는 분류(대기면 None)
      ledger_source  — 확정 / 내부이체 / 이력 / 규칙 / 기존자동 / 소액기본 / 대기
      ledger_state   — 확정 / 자동 / 대기
      ledger_conf    — high / medium / low / ''
      ledger_why     — 사람이 읽는 근거
      ledger_hint    — 대기일 때 제안(확신이 낮아 적용 안 한 것)
      offset_excluded — True 면 재무 숫자에서 빠진다(상쇄)
      offset_tier    — 확실 / 카드취소 / 확정 / 의심 / ''
      later          — '나중에 정하기'로 보낸 거래
    """
    cat_by_id = {c["id"]: c for c in cats}
    cat_by_code = {c["code"]: c for c in cats}
    d = df.copy()
    if d.empty:
        for c in ["ledger_cat_id", "ledger_code", "ledger_cat_name", "ledger_type", "ledger_source", "ledger_state", "ledger_conf", "ledger_why", "ledger_hint",
                  "offset_excluded", "offset_tier", "later"]:
            d[c] = []
        return d
    d["is_auto_categorized"] = d["is_auto_categorized"].fillna(False).astype(bool) if "is_auto_categorized" in d.columns else False
    d["orig_cat_id"] = d["account_category_id"]
    d["orig_confirmed"] = d["orig_cat_id"].notna() & ~d["is_auto_categorized"]

    d = apply_internal_rule(d, cats)                       # 회사 이름 + 짝 확인된 것만 내부이체
    d = mark_duplicates(d, keep_ids, offset_ids)           # ① 확실한 상쇄(같은 날 10분 안) + 대표가 확정한 상쇄
    internal = d["rule_internal"].fillna(False) | d["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("type") == "transfer")
    skip = set(d.loc[d["is_dup_certain"] | internal | d["offset_kept"], "id"])
    d = find_offsets(d, skip_ids=skip)                     # ② 카드 취소 7일 / ③ 의심

    d["offset_tier_final"] = ""
    d.loc[d["offset_tier"] == "의심", "offset_tier_final"] = "의심"
    d.loc[d["offset_tier"] == "카드취소", "offset_tier_final"] = "카드취소"
    d.loc[d["is_dup_certain"], "offset_tier_final"] = "확실"
    d.loc[d["offset_confirmed"], "offset_tier_final"] = "확정"
    d["offset_tier"] = d["offset_tier_final"]
    d = d.drop(columns=["offset_tier_final"])
    d["offset_excluded"] = d["offset_tier"].isin(["확실", "카드취소", "확정"])

    d["ledger_key"] = d["description"].map(clean_key)
    conf_rows = d[d["orig_confirmed"] & ~internal & d["orig_cat_id"].map(lambda x: cat_by_id.get(x, {}).get("code") not in (CATCH_ALL, INTERNAL_CODE) and cat_by_id.get(x, {}).get("type") != "transfer")]
    hist = build_history(conf_rows)

    n = len(d)
    out = {k: [None] * n for k in ["ledger_cat_id", "ledger_source", "ledger_conf", "ledger_why", "ledger_hint"]}
    for pos, r in enumerate(d.to_dict("records")):
        cid = src = conf = why = hint = None
        direction = r["direction"]
        if r["orig_confirmed"] and not bool(r.get("rule_internal")):
            cid, src, conf, why = r["orig_cat_id"], "확정", "high", "대표님이 직접 분류"
        elif bool(r.get("rule_internal")):
            cid, src, conf, why = r["account_category_id"], "내부이체", "high", "회사 이름 + 반대편 거래(짝) 확인"
        else:
            rl0 = rule_lookup(r["description"], direction)
            card_bill = bool(rl0 and rl0[0] == "CARD_BILL" and direction == "out")   # 카드대금은 실제 내역(카드 명세서)을 몰라서 이력으로도 따라가지 않는다
            h = None if card_bill else history_lookup(hist, direction, r["ledger_key"], lambda c: cat_by_id.get(c, {}).get("type"))
            if h and _compatible(direction, cat_by_id.get(h[0], {}).get("type")):
                cid, conf, why = h[0], h[1], h[2]
                src = "이력"
            else:
                rl = rule_lookup(r["description"], direction)
                tgt = cat_by_code.get(rl[0]) if rl else None
                if rl and tgt and _compatible(direction, tgt.get("type")) and rl[1] in ("high", "medium"):
                    cid, src, conf, why = tgt["id"], "규칙", rl[1], rl[2]
                elif rl and (tgt or rl[0] == "CARD_BILL"):
                    hint = f"제안: {tgt['name'] if tgt else '카드대금'} ({rl[2]})"
                existing = r["orig_cat_id"]
                if cid is None and existing is not None and not pd.isna(existing):
                    ecat = cat_by_id.get(existing, {})
                    if ecat.get("code") == CATCH_ALL:
                        cid, src, conf, why = existing, "소액기본", "low", "20만 원 미만 출금의 임시 분류(더 세밀한 근거 없음)"
                    else:
                        cid, src, conf, why = existing, "기존자동", "medium", "DB 자동분류"
        if cid is None:
            src, conf, why = "대기", "", why or "분류 근거를 찾지 못함"
            if bool(r.get("internal_name")) and not hint:
                hint = "회사 이름이 적혔지만 반대편 거래(짝)가 없어요 — 데이터에 없는 회사 계좌·플랫폼 정산일 수 있어요"
        out["ledger_cat_id"][pos], out["ledger_source"][pos], out["ledger_conf"][pos] = cid, src, conf
        out["ledger_why"][pos], out["ledger_hint"][pos] = why, hint
    for k, v in out.items():
        d[k] = v
    d["ledger_code"] = d["ledger_cat_id"].map(lambda x: cat_by_id.get(x, {}).get("code") if x is not None else None)
    d["ledger_cat_name"] = d["ledger_cat_id"].map(lambda x: cat_by_id.get(x, {}).get("name") if x is not None else None)
    d["ledger_type"] = d["ledger_cat_id"].map(lambda x: cat_by_id.get(x, {}).get("type") if x is not None else None)
    d["ledger_state"] = d["ledger_source"].map(lambda s: "확정" if s == "확정" else "대기" if s == "대기" else "자동")
    d["later"] = d["id"].isin(later_ids or set())
    return d


# ── 숫자 ──────────────────────────────────────────────────────

def summarize(d):
    """자동 정리된 거래(상쇄 제외분만 넘길 것)에서 화면 숫자를 계산한다.
    매출·비용은 분류된 것 기준, 분류 대기는 별도 줄. 순현금흐름은 내부이체·상쇄를 뺀 모든 입출금."""
    live = d[~d["offset_excluded"] & (d["ledger_type"] != "transfer")]
    ok = live[live["ledger_state"] != "대기"]
    wait = live[live["ledger_state"] == "대기"]

    def net(df, types, cost=False):
        s = df[df["ledger_type"].isin(types)]
        v = s.loc[s["direction"] == "in", "amount"].sum() - s.loc[s["direction"] == "out", "amount"].sum()
        return -v if cost else v

    rev = net(ok, ["revenue"])
    cost = net(ok, ["cost_cogs", "cost_sga", "labor"], cost=True)
    other = net(ok, ["other"])
    wait_in = wait.loc[wait["direction"] == "in", "amount"].sum()
    wait_out = wait.loc[wait["direction"] == "out", "amount"].sum()
    cash_in = live.loc[live["direction"] == "in", "amount"].sum()
    cash_out = live.loc[live["direction"] == "out", "amount"].sum()
    trust = {"확정": float(live.loc[live["ledger_state"] == "확정", "amount"].sum()),
             "자동": float(live.loc[live["ledger_state"] == "자동", "amount"].sum()),
             "대기": float(live.loc[live["ledger_state"] == "대기", "amount"].sum())}
    tot = sum(trust.values()) or 1.0
    return {"revenue": float(rev), "cost": float(cost), "op_profit": float(rev - cost), "other": float(other),
            "wait_in": float(wait_in), "wait_out": float(wait_out), "wait_n_in": int((wait["direction"] == "in").sum()), "wait_n_out": int((wait["direction"] == "out").sum()),
            "cash_in": float(cash_in), "cash_out": float(cash_out), "net_cash": float(cash_in - cash_out),
            "trust": trust, "trust_pct": {k: v / tot for k, v in trust.items()}, "n": int(len(live)),
            "offset_amount": float(d.loc[d["offset_excluded"], "amount"].sum() / 2.0), "offset_n": int(d["offset_excluded"].sum())}


def by_category(d):
    """계정과목별 표: 구분(type)·분류 이름·건수·입금합·출금합·순액·상태별 건수. 상쇄·내부이체 제외, 분류 대기는 방향별로 한 줄씩."""
    live = d[~d["offset_excluded"] & (d["ledger_type"] != "transfer")].copy()
    live["_name"] = live["ledger_cat_name"]
    w = live["ledger_state"] == "대기"
    live.loc[w, "_name"] = live.loc[w, "direction"].map({"in": "? 분류 대기 (입금)", "out": "? 분류 대기 (출금)"})
    live["_type"] = live["ledger_type"].where(~w, "wait")
    rows = []
    for (typ, name), g in live.groupby(["_type", "_name"]):
        rows.append({"type": typ, "name": name, "n": len(g),
                     "in": float(g.loc[g["direction"] == "in", "amount"].sum()), "out": float(g.loc[g["direction"] == "out", "amount"].sum()),
                     "n_confirmed": int((g["ledger_state"] == "확정").sum()), "n_auto": int((g["ledger_state"] == "자동").sum()), "n_wait": int((g["ledger_state"] == "대기").sum())})
    return pd.DataFrame(rows)


# ── 정확도 점검(백테스트) ─────────────────────────────────────

def backtest(df, cats):
    """대표님이 확정한 거래를 하나씩 가리고(leave-one-out) 엔진이 맞히는지 본다. 반환 dict.
    history: 같은 거래처 이력으로 맞힌 비율 / rules: 키워드 규칙이 맞힌 비율."""
    cat_by_id = {c["id"]: c for c in cats}
    cat_by_code = {c["code"]: c for c in cats}
    d = df.copy()
    d["is_auto_categorized"] = d["is_auto_categorized"].fillna(False).astype(bool)
    conf = d[d["account_category_id"].notna() & ~d["is_auto_categorized"]].copy()
    ok = conf["account_category_id"].map(lambda x: cat_by_id.get(x, {}).get("type") != "transfer" and cat_by_id.get(x, {}).get("code") != CATCH_ALL).astype(bool)
    conf = conf[ok].copy()
    conf["ledger_key"] = conf["description"].map(clean_key)
    hist = build_history(conf)
    h_n = h_ok = h_amt = h_amt_ok = 0
    r_n = r_ok = 0
    for r in conf.itertuples():
        h = hist[(r.direction, r.ledger_key)]
        h[r.account_category_id] -= 1                        # 이 거래만 가린다
        if h[r.account_category_id] == 0:
            del h[r.account_category_id]
        got = history_lookup({k: v for k, v in hist.items() if v}, r.direction, r.ledger_key, lambda c: cat_by_id.get(c, {}).get("type"))
        h[r.account_category_id] = h.get(r.account_category_id, 0) + 1
        if got:
            h_n += 1
            h_amt += r.amount
            if got[0] == r.account_category_id:
                h_ok += 1
                h_amt_ok += r.amount
        rl = rule_lookup(r.description, r.direction)
        tgt = cat_by_code.get(rl[0]) if rl else None
        if rl and tgt and rl[1] in ("high", "medium"):
            r_n += 1
            if tgt["id"] == r.account_category_id:
                r_ok += 1
    total = len(conf)
    return {"confirmed": total, "hist_covered": h_n, "hist_correct": h_ok, "hist_cover_pct": h_n / total if total else 0, "hist_acc": h_ok / h_n if h_n else 0,
            "rule_covered": r_n, "rule_correct": r_ok, "rule_cover_pct": r_n / total if total else 0, "rule_acc": r_ok / r_n if r_n else 0}


# ── 상쇄 보관함 ───────────────────────────────────────────────

def offset_pairs(d):
    """상쇄·상쇄 의심 거래를 '한 쌍 = 한 줄'로 묶는다. 반환 DataFrame: tier, key, ids, account, amount, out_date, in_date, out_desc, in_desc, gap_days, excluded"""
    cols = ["tier", "key", "ids", "account", "amount", "out_date", "in_date", "out_desc", "in_desc", "gap_days", "excluded"]
    x = d[d["offset_tier"] != ""]
    if x.empty:
        return pd.DataFrame(columns=cols)
    x = x.assign(_key=x["offset_pair_id"].where(x["offset_pair_id"].notna(), x["offset_pair"]).where(lambda s: s.notna(), x["id"]))
    by = {}
    for r in x.to_dict("records"):
        by.setdefault(r["_key"], []).append(r)
    rows = []
    for key, g in by.items():
        o = next((r for r in g if r["direction"] == "out"), None)
        i = next((r for r in g if r["direction"] == "in"), None)
        ref = o if o is not None else i
        gap = None
        if o is not None and i is not None and pd.notna(o["txn_date"]) and pd.notna(i["txn_date"]):
            gap = abs((i["txn_date"] - o["txn_date"]).days)
        rows.append({"tier": g[0]["offset_tier"], "key": str(key), "ids": [r["id"] for r in g], "account": ref["account_label"], "amount": float(ref["amount"]),
                     "out_date": o["txn_date"] if o is not None else pd.NaT, "in_date": i["txn_date"] if i is not None else pd.NaT,
                     "out_desc": (o["description"] if o is not None else "") or "", "in_desc": (i["description"] if i is not None else "") or "",
                     "gap_days": gap, "excluded": bool(g[0]["offset_excluded"])})
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values(["excluded", "out_date"], ascending=[False, False]).reset_index(drop=True)
