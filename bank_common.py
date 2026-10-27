"""은행거래 공통 계산 (재무캘린더·부가세 신고 준비가 함께 쓴다). streamlit 에 의존하지 않는다.

'상쇄' 규칙(10/12 대표 정정): 같은 계좌·같은 날·같은 금액의 입금과 출금이 몇 분 안에 짝을 이루면 결제 ↔ 취소로 본다.
"""
import unicodedata

import pandas as pd

OFFSET_WINDOW_MIN = 10   # 입금·출금이 이 분 안에 있어야 '결제 ↔ 취소' 상쇄로 본다


def mark_duplicates(df, keep_ids=None, offset_ids=None):
    """'상쇄' = 같은 계좌에서 같은 날, 같은 금액의 입금과 출금이 몇 분 차이로 짝을 이룬 것 (결제 ↔ 취소/환불). (DB는 안 바꾸고 화면용 플래그만 계산)
    - is_dup_certain(=상쇄): 계좌+금액+날짜가 같고 방향이 반대(입금↔출금)이며 거래 시각 차이가 OFFSET_WINDOW_MIN분 이하인 쌍.
        가장 가까운 것끼리 1:1로 짝지어 둘 다 집계에서 뺀다. 짝이 없는 거래는 그대로 반영된다.
    - is_dup_suspect(=애매): 같은 날·같은 금액의 입금/출금이 있는데 시간 차이가 크거나 시각을 몰라서 짝을 못 지은 거래 → 집계에는 포함, 화면에 표시만
    - 같은 방향(출금/출금, 입금/입금)끼리는 상쇄가 아니다. (같은 거래의 이중 입력은 dedup_hash 가 이미 막는다)
    - keep_ids: 대표님이 '상쇄 아님(별개 거래)'으로 정한 거래 id → 자동 판단에서 완전히 뺀다
    - offset_ids: 대표님이 '상쇄 확정(재무 미반영)'으로 일괄처리한 거래 id → 상쇄로 고정(offset_confirmed=True). 자동 짝지음에서도 뺀다
    - offset_pair: 자동으로 짝지어진 두 거래가 같은 값을 갖는 쌍 키(일괄처리 화면에서 '한 쌍 = 한 줄'로 보여주는 데 씀), pair_gap_min: 두 거래 시간 차(분)
    """
    df = df.copy()
    keep_ids = keep_ids or set()
    offset_ids = offset_ids or set()
    df["is_dup_certain"] = False
    df["is_dup_suspect"] = False
    df["dup_group"] = None
    df["offset_kept"] = df["id"].isin(keep_ids)
    df["offset_confirmed"] = df["id"].isin(offset_ids)
    df["offset_pair"] = None
    df["pair_gap_min"] = None
    if df.empty:
        return df
    ts = pd.to_datetime(df["txn_datetime"], errors="coerce", utc=True, format="mixed") if "txn_datetime" in df.columns else pd.Series(pd.NaT, index=df.index)
    pool = df[~df["offset_kept"] & ~df["offset_confirmed"]]
    for gkey, g in pool.groupby(["account_label", "amount", "txn_date"], dropna=True):
        ins, outs = g.index[g["direction"] == "in"], g.index[g["direction"] == "out"]
        if len(ins) == 0 or len(outs) == 0:
            continue
        cand = sorted(
            (abs((ts[i] - ts[o]).total_seconds()), i, o)
            for i in ins for o in outs if pd.notna(ts[i]) and pd.notna(ts[o])
        )
        used, matched = set(), []
        for gap, i, o in cand:
            if gap > OFFSET_WINDOW_MIN * 60:
                break
            if i in used or o in used:
                continue
            used.update((i, o)); matched += [i, o]
            key = "|".join(sorted([str(df.at[i, "id"]), str(df.at[o, "id"])]))
            df.at[i, "offset_pair"] = key; df.at[o, "offset_pair"] = key
            df.at[i, "pair_gap_min"] = round(gap / 60, 1); df.at[o, "pair_gap_min"] = round(gap / 60, 1)
        left_in = [i for i in ins if i not in used]
        left_out = [o for o in outs if o not in used]
        if matched:
            df.loc[matched, "is_dup_certain"] = True
        if left_in and left_out:   # 같은 날·같은 금액인데 짝을 못 지은 입금/출금 → 애매
            df.loc[left_in + left_out, "is_dup_suspect"] = True
        if matched or (left_in and left_out):   # 화면에서 한 묶음으로 보이게(짝 없는 거래도 '남음'으로 함께 표시)
            df.loc[g.index, "dup_group"] = str(gkey)
    df.loc[df["offset_confirmed"], "is_dup_certain"] = True   # 대표님이 확정한 상쇄는 항상 상쇄
    return df


def add_confirm_state(df):
    """은행거래 한 줄마다 '확정/미확정'과 미확정 이유를 붙인다(재무 화면의 '확정만 / 미확정 포함' 보기와 재무 미션이 쓴다).
    · 확정 = 대표님이 직접 분류했거나(자동분류 아님) 명확한 상쇄(자동 제외). 정리할 문제(애매 의심)가 남아 있지 않은 것.
    · 미확정 이유: 분류 필요(미분류) / 자동분류 확인(소액 자동분류) / 애매 확인(같은 날 같은 금액의 반대 거래)
    · 명확한 상쇄(같은 계좌·같은 날·같은 금액·반대 방향·10분 안)는 확인 없이 자동으로 재무에서 빠진다.
    미확정이어도 숫자에는 그대로 반영한다(대표님 요청: 미루면 부정확해지는 것보다 '미확정이지만 다 반영된 표')."""
    d = df.copy()
    if d.empty:
        d["confirm_state"] = []
        d["pending_reason"] = []
        return d
    auto = d["is_auto_categorized"].fillna(False).astype(bool) if "is_auto_categorized" in d.columns else pd.Series(False, index=d.index)
    uncategorized = d["account_category_id"].isna()
    reason = pd.Series("", index=d.index)
    reason = reason.mask(auto & ~uncategorized, "자동분류 확인")
    reason = reason.mask(uncategorized & ~d["is_dup_certain"], "분류 필요")
    reason = reason.mask(d["is_dup_suspect"], "애매 확인")
    reason = reason.mask(d["is_dup_certain"], "")   # 명확한 상쇄(자동)는 확인할 게 없다 — 재무에 안 들어가고 목록에도 안 나온다(10/23 대표)
    d["pending_reason"] = reason
    d["confirm_state"] = reason.map(lambda r: "미확정" if r else "확정")
    return d


def parse_txn_datetime(series):
    """은행마다 거래시각 표기가 다르다(SC제일 '2025.10.07 12:42:04' / 기업 '2025-11-19 14:21:23').
    첫 줄 형식으로 통째로 읽으면 나머지가 빈 값이 되므로 형식을 섞어서 읽는다."""
    return pd.to_datetime(series.astype("string").str.replace(".", "-", regex=False), errors="coerce", format="mixed")


# ── 회사 자기 이름이 적요에 있으면 '내부 자금이동'(10/19 대표 지시: 브랜드슬램으로 입금된 것은 매출이 아니라 내부이체) ──
INTERNAL_NAME = "브랜드슬"      # 은행이 이름을 앞 몇 글자만 줘서('기업－（주）브랜드슬') '브랜드슬'까지만 본다
INTERNAL_CODE = "TRANSFER_INTERNAL"


def is_internal_desc(desc):
    """적요에 우리 회사 이름이 있으면 True. 전각 문자(（주）·－)는 반각으로 바꿔서 본다."""
    return INTERNAL_NAME in unicodedata.normalize("NFKC", str(desc or "")).replace(" ", "")


def internal_pairing(df):
    """회사 이름이 적힌 거래(입금·출금)마다 '반대편 거래'(같은 금액·반대 방향·하루 안)가 데이터에 있는지 본다.
    내부이체라면 보내는 쪽 출금과 받는 쪽 입금이 짝으로 보여야 한다(10/22 대표: 보내는 사람·받는 사람이 모두 브랜드슬램).
    반환: internal_name(회사 이름 적힘) · internal_pair('다른 계좌' / '같은 계좌' / '짝 없음' / '')"""
    d = df.copy()
    d["internal_name"] = d["description"].map(is_internal_desc) if not d.empty else pd.Series(dtype=bool)
    d["internal_pair"] = ""
    if d.empty or not d["internal_name"].any():
        return d
    sub = d[d["internal_name"]].copy()
    sub["_day"] = pd.to_datetime(sub["txn_date"], errors="coerce")   # 문자열로 들어와도 날짜로 비교
    for amt, g in sub.groupby("amount"):
        for idx, r in g.iterrows():
            opp = g[(g["direction"] != r["direction"]) & ((g["_day"] - r["_day"]).abs() <= pd.Timedelta(days=1))]
            if opp.empty:
                d.at[idx, "internal_pair"] = "짝 없음"
            elif (opp["account_label"] != r["account_label"]).any():
                d.at[idx, "internal_pair"] = "다른 계좌"
            else:
                d.at[idx, "internal_pair"] = "같은 계좌"
    return d


def apply_internal_rule(df, cats):
    """회사 이름이 적힌 거래 중 **반대편 거래(짝)가 확인되는 것**을 '계좌간 자금이동(내부이체)'으로 본다 → 매출·비용에 안 들어가고 손익계산서에서 빠진다.
    · 짝이 없는 거래(보내는 쪽/받는 쪽 한쪽만 보임)는 자동으로 바꾸지 않는다 — 데이터에 없는 다른 계좌에서 온 돈일 수 있어서 대표님이 확인해야 한다(재무 미션의 '내부이체 짝 점검').
    · DB는 바꾸지 않는다(화면 계산용). 이미 대표님이 다른 계정과목으로 확정한 거래는 건드리지 않는다(미분류·자동분류만 바꿈).
    · DB 트리거(trg_auto_tag_internal_transfer)는 입력되는 순간 적요만 보고 분류하므로 짝 확인은 이 화면 계산이 맡는다.
    반환: 같은 DataFrame(복사본) + rule_internal / internal_name / internal_pair 컬럼"""
    d = internal_pairing(df)
    d["rule_internal"] = False
    if d.empty:
        return d
    tcat = next((c for c in cats if c.get("code") == INTERNAL_CODE), None)
    if not tcat:
        return d
    auto = d["is_auto_categorized"].fillna(False).astype(bool) if "is_auto_categorized" in d.columns else pd.Series(False, index=d.index)
    target = d["internal_name"] & d["internal_pair"].isin(["다른 계좌", "같은 계좌"]) & (d["account_category_id"].isna() | auto)
    d.loc[target, "account_category_id"] = tcat["id"]
    if "cat_name" in d.columns:
        d.loc[target, "cat_name"] = tcat["name"]
    if "cat_type" in d.columns:
        d.loc[target, "cat_type"] = tcat.get("type", "transfer")
    if "is_auto_categorized" in d.columns:
        d.loc[target, "is_auto_categorized"] = False
    d.loc[target, "rule_internal"] = True
    return d


def unmatched_internal(df):
    """회사 이름이 적혔는데 반대편 거래가 없는 거래(점검 대상). 최근 날짜순."""
    if df.empty or "internal_pair" not in df.columns:
        return df.iloc[0:0]
    return df[df["internal_pair"] == "짝 없음"].sort_values("txn_date", ascending=False)
