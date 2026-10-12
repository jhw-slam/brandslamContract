-- 재무캘린더 '상쇄' 처리의 예외 저장 — "이 거래들은 상쇄가 아니라 별개의 정상 거래" 라고 대표님이 정한 것
-- AGENTS.md 규칙 9번: SQL 파일 → 설명 → DB 적용 → 코드 배포
--
-- 변경 내용: 새 테이블 1개를 "추가"만 합니다. bank_transactions 등 기존 테이블·데이터는 건드리지 않습니다.
--
-- 배경 (2026-10-12 대표 설명):
--   같은 계좌·방향·금액·날짜·적요인 은행거래가 2건 있으면 '같은 거래가 두 번 들어온 중복'이 아니라
--   '결제 ↔ 취소'가 서로 지워지는 상쇄 쌍이다. (예: 주유 15만원 결제 → 취소 → 실결제 = 3건 → 앞의 2건이 상쇄, 마지막 1건만 남음)
--   그래서 화면은 시간순으로 2건씩 짝지어 둘 다 집계에서 빼고, 홀수 개면 마지막 1건만 남긴다. (데이터는 지우지 않는다)
--   다만 실제로는 따로 일어난 같은 금액 거래일 수도 있으므로, 그때 대표님이 '상쇄 아님'을 누르면 이 테이블에 기록하고
--   그 거래들은 자동 상쇄 판단에서 영구히 빠진다. (다시 자동 판단에 맡기려면 이 테이블에서 해당 줄을 지우면 된다)
--
-- 칸 설명:
--   bank_transaction_id  예외로 정한 은행거래 1건(행마다 1줄)
--   decision             지금은 'keep'(상쇄 아님 = 정상 거래로 집계)만 사용
--   note / decided_by / decided_at  메모·결정자·결정시각
--
-- 보안: RLS 켬(정책 없음). 서비스 키로만 접근.
-- 되돌리기: DROP TABLE public.bank_offset_overrides;  (그러면 모든 거래가 다시 자동 상쇄 판단을 받는다)
-- 참고: 이 테이블이 없어도 화면은 열리고 자동 상쇄만 동작합니다('상쇄 아님' 저장만 안 됨).

CREATE TABLE IF NOT EXISTS public.bank_offset_overrides (
  bank_transaction_id uuid PRIMARY KEY REFERENCES public.bank_transactions(id) ON DELETE CASCADE,
  decision            text NOT NULL DEFAULT 'keep' CHECK (decision IN ('keep')),
  note                text,
  decided_by          text,
  decided_at          timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.bank_offset_overrides ENABLE ROW LEVEL SECURITY;
