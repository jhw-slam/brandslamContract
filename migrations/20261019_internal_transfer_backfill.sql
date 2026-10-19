-- 이미 들어 있는 '브랜드슬램' 거래 중 미분류·소액 자동분류인 것을 내부이체로 바꿉니다. (데이터 수정 — 규칙 4: 대상 건수 먼저 확인)
-- 2026-10-19 점검 기준 대상 = 50건:
--   입금 미분류 6건 ₩17,222,500 · 출금 미분류 37건 ₩44,800,000 · 출금 소액 자동분류 7건 ₩1,080,000
-- 대표님이 직접 다른 계정과목으로 확정한 거래는 대상이 아닙니다(미분류 또는 자동분류만).
-- 먼저 이 SELECT 로 건수를 확인하세요:
--   SELECT direction, count(*), sum(amount) FROM public.bank_transactions
--   WHERE position('브랜드슬' in regexp_replace(translate(description, '（）－　', '()- '), '\s+', '', 'g')) > 0
--     AND (account_category_id IS NULL OR is_auto_categorized IS TRUE) GROUP BY 1;
-- 되돌리기가 필요하면 이 거래들의 account_category_id 를 NULL 로 되돌리면 됩니다(대상 id 는 실행 전에 따로 보관하세요).

UPDATE public.bank_transactions
SET account_category_id = (SELECT id FROM public.fin_account_categories WHERE code = 'TRANSFER_INTERNAL' LIMIT 1),
    is_auto_categorized = false
WHERE position('브랜드슬' in regexp_replace(translate(description, '（）－　', '()- '), '\s+', '', 'g')) > 0
  AND (account_category_id IS NULL OR is_auto_categorized IS TRUE);
