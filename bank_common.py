"""은행거래 공통 계산 (재무캘린더·부가세 신고 준비가 함께 쓴다). streamlit 에 의존하지 않는다.

'상쇄' 규칙(10/12 대표 정정): 같은 계좌·같은 날·같은 금액의 입금과 출금이 몇 분 안에 짝을 이루면 결제 ↔ 취소로 본다.
"""
import pandas as pd

OFFSET_WINDOW_MIN = 10   # 입금·출금이 이 분 안에 있어야 '결제 ↔ 취소' 상쇄로 본다


def mark_duplicates(df, keep_ids=None):
    """'상쇄' = 같은 계좌에서 같은 날, 같은 금액의 입금과 출금이 몇 분 차이로 짝을 이룬 것 (결제 ↔ 취소/환불). (DB는 안 바꾸고 화면용 플래그만 계산)
    - is_dup_certain(=상쇄): 계좌+금액+날짜가 같고 방향이 반대(입금↔출금)이며 거래 시각 차이가 OFFSET_WINDOW_MIN분 이하인 쌍.
        가장 가까운 것끼리 1:1로 짝지어 둘 다 집계에서 뺀다. 짝이 없는 거래는 그대로 반영된다.
    - is_dup_suspect(=애매): 같은 날·같은 금액의 입금/출금이 있는데 시간 차이가 크거나 시각을 몰라서 짝을 못 지은 거래 → 집계에는 포함, 화면에 표시만
    - 같은 방향(출금/출금, 입금/입금)끼리는 상쇄가 아니다. (같은 거래의 이중 입력은 dedup_hash 가 이미 막는다)
    - keep_ids: 대표님이 '상쇄 아님(별개 거래)'으로 정한 거래 id → 자동 판단에서 완전히 뺀다
    """
    df = df.copy()
    keep_ids = keep_ids or set()
    df["is_dup_certain"] = False
    df["is_dup_suspect"] = False
    df["dup_group"] = None
    df["offset_kept"] = df["id"].isin(keep_ids)
    if df.empty:
        return df
    ts = pd.to_datetime(df["txn_datetime"], errors="coerce", utc=True, format="mixed") if "txn_datetime" in df.columns else pd.Series(pd.NaT, index=df.index)
    pool = df[~df["offset_kept"]]
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
        left_in = [i for i in ins if i not in used]
        left_out = [o for o in outs if o not in used]
        if matched:
            df.loc[matched, "is_dup_certain"] = True
        if left_in and left_out:   # 같은 날·같은 금액인데 짝을 못 지은 입금/출금 → 애매
            df.loc[left_in + left_out, "is_dup_suspect"] = True
        if matched or (left_in and left_out):   # 화면에서 한 묶음으로 보이게(짝 없는 거래도 '남음'으로 함께 표시)
            df.loc[g.index, "dup_group"] = str(gkey)
    return df


def parse_txn_datetime(series):
    """은행마다 거래시각 표기가 다르다(SC제일 '2025.10.07 12:42:04' / 기업 '2025-11-19 14:21:23').
    첫 줄 형식으로 통째로 읽으면 나머지가 빈 값이 되므로 형식을 섞어서 읽는다."""
    return pd.to_datetime(series.astype("string").str.replace(".", "-", regex=False), errors="coerce", format="mixed")
