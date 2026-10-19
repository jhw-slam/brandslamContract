-- 상쇄 '일괄처리'(재무에 미반영으로 확정)를 기록할 수 있게 허용값을 하나 추가합니다.
-- AGENTS.md 규칙 9번: SQL 파일 → 설명 → DB 적용 → 코드 배포
--
-- 변경 내용: bank_offset_overrides.decision 에 허용값 'offset'(상쇄 확정)을 추가합니다. 기존 'keep'(상쇄 아님=정상 거래)은 그대로입니다.
-- 테이블·칸·데이터는 바뀌지 않고, 허용되는 값의 목록(CHECK 제약)만 넓힙니다. 여러 번 실행해도 안전합니다.
--
-- 배경(2026-10-19 대표 요청):
--   자동으로 찾은 상쇄(같은 날·같은 금액의 입금↔출금이 10분 안) 쌍을 20쌍씩 표로 보고,
--   이상 없으면(기본 체크됨) '일괄처리' 한 번으로 확정한다. 확정된 상쇄는 재무 숫자에 반영하지 않고 이 테이블에만 기록이 남는다(데이터는 지우지 않음).
--   특이사항으로 체크를 푼 쌍은 'keep'(상쇄 아님=정상 거래)으로 기록돼 이후 자동 상쇄 판단에서 빠진다.
--
-- 되돌리기: 'offset' 행을 지우고 제약을 원래대로(CHECK (decision = 'keep'))
--   DELETE FROM public.bank_offset_overrides WHERE decision = 'offset';
--   ALTER TABLE public.bank_offset_overrides DROP CONSTRAINT bank_offset_overrides_decision_check;
--   ALTER TABLE public.bank_offset_overrides ADD CONSTRAINT bank_offset_overrides_decision_check CHECK (decision = 'keep');
-- 참고: 이 변경을 적용하기 전에도 화면은 열리지만 '상쇄 일괄처리' 저장은 막히고 안내가 나옵니다.

ALTER TABLE public.bank_offset_overrides DROP CONSTRAINT IF EXISTS bank_offset_overrides_decision_check;
ALTER TABLE public.bank_offset_overrides ADD CONSTRAINT bank_offset_overrides_decision_check CHECK (decision IN ('keep', 'offset'));
