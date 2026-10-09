-- 대표 OKR 2026 Q4 초안 시드 (CEO_OKR_PAGE_SPEC.md 3장 기준)
-- 반드시 20261009_ceo_command_center.sql 을 먼저 적용한 뒤 실행하세요.
--
-- 원칙: 현재값(current_value)은 일부러 비워 둡니다. 값이 없으면 화면에 회색 '입력'으로 보이고, 가짜 숫자는 만들지 않습니다.
--       source='auto' 인 KR 은 화면이 DB에서 직접 계산해서 보여주므로 current_value 를 쓰지 않습니다.
-- 여러 번 실행해도 중복되지 않습니다(ON CONFLICT DO NOTHING).
-- 수치·목표일은 대화 기반 초안입니다. 확정되지 않은 값은 화면에서 직접 고치세요.

INSERT INTO public.ceo_okr_objectives (quarter, code, title, tag, sort) VALUES
  ('2026Q4', 'O1', '내 시간이 가장 비싼 곳에만 쓴다', '대표 시간 레버리지', 1),
  ('2026Q4', 'O2', '받을 돈을 받고, 마진이 남는 구조로', '현금', 2),
  ('2026Q4', 'O3', '일의 수준이 기준 위로 올라온다', '팀 자립', 3),
  ('2026Q4', 'O4', '하나만 골라 집중한다', '다음 성장축', 4)
ON CONFLICT (quarter, code) DO NOTHING;

INSERT INTO public.ceo_okr_krs (objective_id, code, title, metric_type, target_value, due_date, source, display_style, note, sort)
SELECT o.id, k.code, k.title, k.metric_type, k.target_value, k.due_date::date, k.source, k.display_style, k.note, k.sort
FROM (VALUES
  ('O1', 'KR1.1', '기준표 확정 (가이드라인·송금 등록·캠페인 등록)', 'check',        3,           '2026-12-31', 'manual', 'dots',      '3종 채점/체크 기준', 1),
  ('O1', 'KR1.2', '팀원 1:1 주 1회 (김선재·곽재선·구정회·이단우)', 'check',          4,           NULL,         'manual', 'dots',      '이번 주 몇 명과 했는지', 2),
  ('O1', 'KR1.3', '같은 피드백 반복 횟수',                      'number',          0,           NULL,         'manual', 'bignum',    '3주 연속 0이 목표', 3),
  ('O2', 'KR2.1', '받을 돈 회수',                              'number',          0,           '2026-12-31', 'auto',   'bignum',    '화면이 받을 돈(예정) 합계를 직접 계산', 1),
  ('O2', 'KR2.2', '기한 경과 미수',                            'days_overdue',    0,           '2026-10-31', 'auto',   'countdown', '화면이 경과일을 직접 계산', 2),
  ('O2', 'KR2.3', '마진율 자동 산출 시스템 가동',                'date',            NULL,        '2026-11-30', 'manual', 'countdown', '캠페인 등록 + 지출 증빙 2개 시스템', 3),
  ('O3', 'KR3.1', '월 입금 매출 (은행 매출 계정 합)',            'number',          100000000,   NULL,         'auto',   'trend',     '시딩형·방문형 캠페인 매출 등 revenue 계정', 1),
  ('O3', 'KR3.2', '곽재선 가이드라인 채점 점수',                 'number',          70,          NULL,         'manual', 'bar',       '70점 + 필수 항목 충족', 2),
  ('O3', 'KR3.3', '운영 브랜드 수',                            'number',          12,          NULL,         'auto',   'bar',       '영업 계정 중 운영중', 3),
  ('O3', 'KR3.4', '인력 판단일 (경력 채용 여부·역할 재설계)',     'date',            NULL,        '2026-11-03', 'manual', 'countdown', NULL, 4),
  ('O4', 'KR4.1', '자체 브랜드 Go/No-Go 선행조건',              'check',           2,           '2026-11-30', 'manual', 'dots',      '인플루언서 3명+ 확보 · 유통사 딜 클로징', 1),
  ('O4', 'KR4.2', '데이터바우처 대비 (공급기관 요건·수요처 점검)',  'date',            NULL,        '2026-12-31', 'manual', 'countdown', '내년 1월 공고 전', 2),
  ('O4', 'KR4.3', '동시 진행 안건 수',                          'number',          NULL,        NULL,         'manual', 'bignum',    '적을수록 좋음', 3)
) AS k(obj_code, code, title, metric_type, target_value, due_date, source, display_style, note, sort)
JOIN public.ceo_okr_objectives o ON o.quarter = '2026Q4' AND o.code = k.obj_code
ON CONFLICT (objective_id, code) DO NOTHING;

-- 자체 브랜드(유통 겸비) 런칭: 선행조건 2개와 판단일 (해야 할 일은 화면에서 계속 추가)
INSERT INTO public.ceo_brand_launch_tasks (kind, title, owner, due_date, status, note)
SELECT v.kind, v.title, v.owner, v.due_date::date, v.status, v.note
FROM (VALUES
  ('prerequisite', '인플루언서 3명 이상 확보', NULL, '2026-11-30', 'todo', '자체 브랜드 론칭 전 선행조건'),
  ('prerequisite', '유통사 딜 클로징',          NULL, '2026-11-30', 'todo', '자체 브랜드 론칭 전 선행조건'),
  ('milestone',    '자체 브랜드 Go / No-Go 판단', '장현우', '2026-11-30', 'todo', NULL)
) AS v(kind, title, owner, due_date, status, note)
WHERE NOT EXISTS (SELECT 1 FROM public.ceo_brand_launch_tasks);
