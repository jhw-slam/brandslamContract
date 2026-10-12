-- 받을 돈 확인에 '부분입금'과 '계약취소'를 추가 (ceo_receivable_checks 확장)
-- AGENTS.md 규칙 9번: SQL 파일 → 설명 → DB 적용 → 코드 배포
--
-- 변경 내용 (이 SQL이 만드는 테이블 1개만 수정. 기존 재무 테이블·데이터는 그대로):
--   1) status 에 'partial'(부분입금), 'canceled'(계약취소) 값을 허용
--        기존: received(받음) · pending(아직) · disputed(분쟁/협의)
--        변경: received · partial · pending · canceled · disputed
--   2) received_amount 컬럼 추가: 부분입금일 때 '지금까지 받은 금액(누적)'을 적는다.
--        남은 금액 = 예정 금액 − received_amount (화면이 계산)
--
-- 의미 (cash_events 자체는 바꾸지 않는다 — 재무 완료는 은행거래 매칭으로만):
--   received  전액 받음      → 받을 돈에서 빠짐
--   partial   일부만 받음    → 남은 금액만 받을 돈에 남음
--   pending   아직 못 받음   → 그대로 남고, 14일 뒤 다시 '체크 필요'
--   canceled  계약 취소      → 받을 돈에서 빠짐(되돌리기 가능)
-- 되돌리기: ALTER TABLE ... DROP COLUMN received_amount; 제약을 이전 3개 값으로 되돌리면 원상복구.

ALTER TABLE public.ceo_receivable_checks
  ADD COLUMN IF NOT EXISTS received_amount numeric CHECK (received_amount IS NULL OR received_amount >= 0);

ALTER TABLE public.ceo_receivable_checks DROP CONSTRAINT IF EXISTS ceo_receivable_checks_status_check;
ALTER TABLE public.ceo_receivable_checks
  ADD CONSTRAINT ceo_receivable_checks_status_check
  CHECK (status IN ('received', 'partial', 'pending', 'canceled', 'disputed'));
