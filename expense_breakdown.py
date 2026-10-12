"""재무캘린더 '세부사항 보기'가 쓰는 계산. streamlit 에 의존하지 않는다(테스트 쉬움).

은행 적요(거래처 이름)를 묶어서 '무엇에 얼마를 썼는지' 보이게 하고, 키워드로 세부 분류를 '제안'만 한다.
· 제안은 저장하지 않는다(대표님이 확인하기 전에는 DB의 분류를 바꾸지 않는다).
· 확신이 낮은 건 '확인 필요'로 표시하고, 근거가 없으면 억지로 분류하지 않는다.
· 급여·인건비는 개인별로 보여주지 않는다(월별 합계만).
"""
import re
import unicodedata

# (키워드 정규식, 제안 계정코드, 확신도, 근거). 위에서부터 먼저 맞는 규칙이 적용된다.
RULES = [
    (r"브랜드슬", "TRANSFER_INTERNAL", "high", "회사 이름이 적요에 있음(내부이체 가능성)"),
    (r"PAYPAL", "COGS_INFLUENCER", "medium", "페이팔 결제 — 해외 인플루언서 지급일 수 있음(확인 필요)"),
    (r"NOTION|APIFY|SUPABASE|MAILMETEOR|GOOGLE|APPLE|RAILWAY|ANTHROPIC|OPENAI|SLACK|ZOOM|CANVA|FIGMA|GITHUB|CLAUDE|KT인터넷|인터넷제휴", "SGA_SUBSCRIPTION", "high", "SaaS·통신 구독"),
    (r"세무사|법무|법률|회계", "SGA_OUTSOURCING_FEE", "high", "세무·법률 대리 수수료"),
    (r"대표인증", "SGA_OUTSOURCING_FEE", "medium", "인증서·은행 수수료성"),
    (r"국세|세무서|지방세|지방소득세", "SGA_TAXES_DUES", "low", "세금 납부(원천세·부가세 등 구분 필요)"),
    (r"삼성카드|현대카드|신한카드|국민카드|롯데카드|비씨카드|하나카드|우리카드|농협카드", "CARD_BILL", "high", "카드대금 결제 — 실제 내역은 카드 명세서가 있어야 알 수 있음"),
    (r"주유|이케이에너지|GS칼텍스|SK에너지|S-OIL|하이패스|도로공사|주차|케이엠파크|카카오T|택시|우버|타다|TADA|코레일|SRT|KTX|에어부산|대한항공|아시아나|항공", "SGA_TRAVEL", "medium", "교통·출장·주유"),
    (r"의원|병원|의학과|마취통증|약국|건강검진", "SGA_WELFARE", "low", "의료(건강검진 등 복리후생인지 확인)"),
    (r"김밥|돈카츠|해장|흑돈|뚜레쥬르|배스킨|GS25|CU|세븐일레븐|스타벅스|커피|카페|식당|참숯|화로|정갈|초록마을|오아시스|컬리|곤드레|국밥|치킨|버거|샐러드|베이커리", "SGA_WELFARE", "low", "식음료(직원 식대=복리후생 / 외부 미팅=접대비 — 확인 필요)"),
    (r"쿠팡|네이버페이|TEMU|아이파크몰|11번가|지마켓|옥션|인터넷상거래", "SGA_OFFICE_SUPPLIES", "low", "온라인 구매(소모품인지 협찬제품 구매인지 확인 필요)"),
    (r"운송|택배|물류|CJ대한|로젠|한진", "COGS_LOGISTICS", "low", "배송·운송(캠페인 배송비인지 확인)"),
]


def normalize_merchant(desc):
    """거래처 묶음용 이름: 전각→반각, 공백·'INV#...' 꼬리 정리. 비어 있으면 '(적요 없음)'."""
    s = unicodedata.normalize("NFKC", str(desc or "")).strip()
    s = re.sub(r"INV#\S*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" *")
    return s or "(적요 없음)"


def suggest_category(desc):
    """(코드, 확신도, 근거) 또는 None. 키워드가 없으면 억지로 정하지 않는다."""
    s = unicodedata.normalize("NFKC", str(desc or "")).upper()
    for pat, code, conf, why in RULES:
        if re.search(pat.upper(), s):
            return code, conf, why
    return None


def group_merchants(rows):
    """rows: dict 목록(description, amount, txn_date). 거래처별 묶음 → 합계 큰 순 목록.
    각 항목: merchant, n, total, avg, last(최근일 문자열), code/conf/why(제안)"""
    by = {}
    for r in rows:
        m = normalize_merchant(r.get("description"))
        g = by.setdefault(m, {"merchant": m, "n": 0, "total": 0.0, "last": None, "sample": r.get("description")})
        g["n"] += 1
        g["total"] += float(r.get("amount") or 0)
        d = str(r.get("txn_date") or "")[:10]
        if d and (g["last"] is None or d > g["last"]):
            g["last"] = d
    out = []
    for g in by.values():
        g["avg"] = g["total"] / g["n"] if g["n"] else 0
        sug = suggest_category(g["merchant"])
        g["code"], g["conf"], g["why"] = sug if sug else (None, None, "근거 부족 — 사람이 확인")
        out.append(g)
    out.sort(key=lambda g: -g["total"])
    return out


def proposal_summary(groups, name_by_code):
    """제안대로 나누면 계정별로 얼마가 되는지. 반환: [(이름, 건수, 합계)] 합계 큰 순. 제안이 없으면 '확인 필요(근거 부족)'."""
    acc = {}
    for g in groups:
        if g["code"] == "CARD_BILL":
            key = "카드대금(명세서 필요)"
        elif g["code"]:
            key = name_by_code.get(g["code"], g["code"]) + ("" if g["conf"] == "high" else " (확인 필요)")
        else:
            key = "확인 필요(근거 부족)"
        a = acc.setdefault(key, [0, 0.0])
        a[0] += g["n"]; a[1] += g["total"]
    return sorted(((k, v[0], v[1]) for k, v in acc.items()), key=lambda x: -x[2])
