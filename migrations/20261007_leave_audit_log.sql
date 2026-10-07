-- 휴가 관리(대표 전용 페이지 pages/15_휴가관리.py)에서 한 수정·취소·삭제·대리등록의 변경 이력 테이블
-- AGENTS.md 규칙 9번: SQL 파일 → 설명 → DB 적용(대표님) → 코드 배포
--
-- 변경 내용: 새 테이블 1개를 "추가"만 합니다. 기존 테이블(leave_profiles, leave_requests)과 데이터는 건드리지 않습니다.
--
-- 왜 필요한가:
--   휴가 기록을 '삭제'하면 leave_requests 에서 행이 사라져서 나중에 "언제 뭘 지웠는지" 알 방법이 없습니다.
--   수정도 마찬가지로 이전 값이 남지 않습니다. 그래서 바꾸기 전 값(before)과 바꾼 값(after)을 통째로 남겨둡니다.
--   삭제를 실수했을 때는 before 값을 보고 다시 등록할 수 있습니다.
--
-- 컬럼 설명:
--   actor      누가 바꿨는지(대표 이메일)
--   person     어느 직원의 휴가인지
--   action     profile_update(입사일/연간 일수 수정) · request_update(날짜·종류·메모 수정)
--              request_cancel(취소) · request_delete(삭제) · request_add(대신 추가)
--   target_id  바뀐 leave_requests.id (프로필 수정이면 비어 있음)
--   before_data / after_data  바꾸기 전 / 바꾼 후 값(JSON). 추가면 before 없음, 삭제면 after 없음.
--
-- 보안: RLS 켬(정책 없음). 이 앱은 서비스 키로만 접근하므로 동작에는 영향 없음. 직원앱은 이 테이블을 읽지 않습니다.
-- 되돌리기: DROP TABLE public.leave_audit_log;
--
-- 참고: 이 테이블이 아직 없어도 휴가 관리 페이지는 정상 동작합니다.
--       다만 저장할 때 "변경 이력을 남기지 못했어요"라는 경고가 화면에 뜹니다.

CREATE TABLE IF NOT EXISTS public.leave_audit_log (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at  timestamptz NOT NULL DEFAULT now(),
  actor       text NOT NULL,
  person      text NOT NULL,
  action      text NOT NULL CHECK (action IN ('profile_update', 'request_update', 'request_cancel', 'request_delete', 'request_add')),
  target_id   uuid,
  before_data jsonb,
  after_data  jsonb
);
CREATE INDEX IF NOT EXISTS leave_audit_log_person_idx  ON public.leave_audit_log (person, created_at DESC);
CREATE INDEX IF NOT EXISTS leave_audit_log_created_idx ON public.leave_audit_log (created_at DESC);

ALTER TABLE public.leave_audit_log ENABLE ROW LEVEL SECURITY;
