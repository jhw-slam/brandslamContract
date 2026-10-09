-- 대표 목표관리 대시보드(Command Center, pages/16_커맨드센터.py)용 테이블
-- AGENTS.md 규칙 9번: SQL 파일 → 설명 → DB 적용(대표님) → 코드 배포
--
-- 변경 내용: 새 테이블 8개를 "추가"만 합니다. 기존 테이블·데이터는 건드리지 않습니다.
-- (cash_events 등 재무 테이블은 읽기만 하고, 입금 완료 표시는 그대로 은행거래 매칭으로만 바뀝니다.)
--
-- ┌ 대표 전용 (ceo_ 로 시작) — 직원 앱(brandslamteamspace)은 이 테이블을 읽지 않습니다
-- │ 1) ceo_okr_weekly_focus  '이번 주 한 가지' 배너 (주 시작일마다 1줄)
-- │ 2) ceo_okr_objectives    분기 목표 O1~O4
-- │ 3) ceo_okr_krs           목표별 KR(핵심결과). 값이 없으면 화면에 '입력'으로 표시(가짜 숫자 금지)
-- │ 4) ceo_okr_checkins      KR 값을 바꿀 때마다 쌓는 이력(추이 그래프·변경 근거)
-- │ 5) ceo_scm_stage_map     SCM 8단계에 OKR 항목을 어느 단계로 볼지 대표님이 고쳐 둔 배치(없으면 코드의 기본 규칙 사용)
-- │ 6) ceo_brand_launch_tasks 자체 브랜드(유통 겸비) 런칭을 위해 해야 할 일·선행조건·판단일
-- │ 7) ceo_receivable_checks 받을 돈(예정) 건을 대표님이 직접 확인한 기록(받음/아직/분쟁).
-- │                          은행 매칭을 안 해도 대표님이 체크하면 화면 숫자가 정확해지도록 '별도 기록'으로 둠.
-- └                          cash_events.paid 는 바꾸지 않습니다(재무 완료는 은행거래 매칭으로만 — AGENTS.md 8번).
--
-- ┌ 직원 입력용 (김선재가 팀스페이스에서 입력하게 될 테이블)
-- └ 8) sales_pipeline_deals  계약예정(예측회의 결과). 브랜드·예상금액·예정월·가능성. 지금은 화면이 0건으로 표시.
--
-- 보안: 8개 모두 RLS 켬(정책 없음). 두 앱 모두 서비스 키로만 접근하므로 동작에는 영향 없음.
-- 되돌리기: DROP TABLE 을 아래 8개 이름으로 실행하면 원상복구(다른 테이블에 영향 없음).
--
-- 참고: 이 SQL을 적용하기 전에도 화면은 열립니다. 해당 칸이 '입력/DB 적용 필요'로 표시될 뿐입니다.

CREATE TABLE IF NOT EXISTS public.ceo_okr_weekly_focus (
  week_start  date PRIMARY KEY,                 -- 그 주 월요일
  headline    text NOT NULL,
  subline     text,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ceo_okr_objectives (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  quarter     text NOT NULL,                    -- 예: '2026Q4'
  code        text NOT NULL,                    -- 'O1' ~ 'O4'
  title       text NOT NULL,
  tag         text,                             -- 카드 위 작은 글씨(예: '대표 시간 레버리지')
  sort        int  NOT NULL DEFAULT 0,
  UNIQUE (quarter, code)
);

CREATE TABLE IF NOT EXISTS public.ceo_okr_krs (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  objective_id  uuid NOT NULL REFERENCES public.ceo_okr_objectives(id) ON DELETE CASCADE,
  code          text NOT NULL,                  -- 'KR3.1'
  title         text NOT NULL,
  metric_type   text NOT NULL CHECK (metric_type IN ('number', 'percent', 'check', 'date', 'days_overdue')),
  target_value  numeric,                        -- 목표(비어 있을 수 있음)
  current_value numeric,                        -- 비어 있으면 화면에 회색 '입력'
  due_date      date,
  source        text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'auto')),  -- auto = 화면이 DB에서 직접 계산
  display_style text NOT NULL DEFAULT 'bignum' CHECK (display_style IN ('bignum', 'bar', 'signal', 'countdown', 'donut', 'trend', 'dots')),
  status        text CHECK (status IN ('red', 'amber', 'green')),
  note          text,
  sort          int  NOT NULL DEFAULT 0,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (objective_id, code)
);

CREATE TABLE IF NOT EXISTS public.ceo_okr_checkins (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kr_id       uuid NOT NULL REFERENCES public.ceo_okr_krs(id) ON DELETE CASCADE,
  value       numeric,
  status      text,
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ceo_okr_checkins_kr_idx ON public.ceo_okr_checkins (kr_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.ceo_scm_stage_map (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  match_kind  text NOT NULL CHECK (match_kind IN ('okr_item', 'category')),   -- 항목 하나 / 분류명 통째로
  match_value text NOT NULL,                                                   -- okr_items.id 또는 okr_items.category
  stage_code  text NOT NULL CHECK (stage_code IN ('S1','S2','S3','S4','S5','S6','S7','S8')),
  note        text,
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (match_kind, match_value)
);

CREATE TABLE IF NOT EXISTS public.ceo_brand_launch_tasks (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind        text NOT NULL DEFAULT 'task' CHECK (kind IN ('prerequisite', 'milestone', 'task')),  -- 선행조건 / 판단일 / 해야 할 일
  title       text NOT NULL,
  owner       text,                             -- 담당(이름)
  due_date    date,
  status      text NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'blocked', 'done')),
  note        text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  done_at     timestamptz
);

CREATE TABLE IF NOT EXISTS public.ceo_receivable_checks (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  cash_event_id  uuid NOT NULL REFERENCES public.cash_events(id) ON DELETE CASCADE,
  status         text NOT NULL CHECK (status IN ('received', 'pending', 'disputed')),  -- 받음 / 아직 / 분쟁·협의
  note           text,
  checked_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ceo_receivable_checks_ev_idx ON public.ceo_receivable_checks (cash_event_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS public.sales_pipeline_deals (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  brand_name      text NOT NULL,
  expected_amount numeric NOT NULL DEFAULT 0 CHECK (expected_amount >= 0),
  currency        text NOT NULL DEFAULT 'KRW',
  expected_month  date,                          -- 예정월(그 달 1일로 저장)
  probability     text NOT NULL DEFAULT '중간' CHECK (probability IN ('확정', '높음', '중간', '낮음')),
  status          text NOT NULL DEFAULT '예정' CHECK (status IN ('예정', '협의중', '확정', '무산')),
  note            text,
  submitted_by    text,                          -- 입력한 직원(김선재)
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sales_pipeline_deals_status_idx ON public.sales_pipeline_deals (status, expected_month);

ALTER TABLE public.ceo_okr_weekly_focus   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ceo_okr_objectives     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ceo_okr_krs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ceo_okr_checkins       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ceo_scm_stage_map      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ceo_brand_launch_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ceo_receivable_checks  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sales_pipeline_deals   ENABLE ROW LEVEL SECURITY;
