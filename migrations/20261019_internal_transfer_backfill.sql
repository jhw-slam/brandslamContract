-- 이미 들어 있는 '브랜드슬램' 거래 중 미분류·소액 자동분류인 것을 내부이체로 바꿉니다. (데이터 수정 — 규칙 4: 대상 건수 먼저 확인)
-- 내부이체 = 보내는 사람과 받는 사람이 모두 브랜드슬램 → 보내는 쪽 출금과 받는 쪽 입금이 '짝'으로 보여야 한다(10/22 대표 기준).
-- 그래서 **반대편 거래(같은 금액·반대 방향·하루 안)가 확인되는 것만** 바꿉니다. 짝이 없는 건 바꾸지 않고 대표님이 확인합니다.
-- 2026-10-22 점검 기준 대상 = 49건: 입금 5건 ₩2,950,000 · 출금 44건 ₩45,880,000 (회사 이름 거래 중 미분류·자동분류 총 50건 중 짝 없는 입금 1건 ₩14,272,500 제외)
-- 대표님이 직접 다른 계정과목으로 확정한 거래는 대상이 아닙니다(미분류 또는 자동분류만).
-- 먼저 이 SELECT 로 건수를 확인하세요(같은 조건):
--   WITH x AS (SELECT id, direction, amount, txn_date, account_category_id, is_auto_categorized,
--                     position('브랜드슬' in regexp_replace(translate(description, '（）－　', '()- '), '\s+', '', 'g')) > 0 AS internal
--              FROM public.bank_transactions)
--   SELECT t.direction, count(*), sum(t.amount) FROM x t
--   WHERE t.internal AND (t.account_category_id IS NULL OR t.is_auto_categorized IS TRUE)
--     AND EXISTS (SELECT 1 FROM x b WHERE b.internal AND b.direction <> t.direction AND b.amount = t.amount AND abs(b.txn_date - t.txn_date) <= 1 AND b.id <> t.id)
--   GROUP BY 1;
-- 되돌리기가 필요하면 이 거래들의 account_category_id 를 NULL 로 되돌리면 됩니다(대상 id 는 실행 전에 따로 보관하세요).

WITH x AS (
  SELECT id, direction, amount, txn_date, account_category_id, is_auto_categorized,
         position('브랜드슬' in regexp_replace(translate(description, '（）－　', '()- '), '\s+', '', 'g')) > 0 AS internal
  FROM public.bank_transactions
)
UPDATE public.bank_transactions bt
SET account_category_id = (SELECT id FROM public.fin_account_categories WHERE code = 'TRANSFER_INTERNAL' LIMIT 1),
    is_auto_categorized = false
FROM x t
WHERE bt.id = t.id
  AND t.internal
  AND (t.account_category_id IS NULL OR t.is_auto_categorized IS TRUE)
  AND EXISTS (SELECT 1 FROM x b WHERE b.internal AND b.direction <> t.direction AND b.amount = t.amount AND abs(b.txn_date - t.txn_date) <= 1 AND b.id <> t.id);
