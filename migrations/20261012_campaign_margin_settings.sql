-- 캠페인 마진율 관리(CFO › Campaign Margin, pages/18_캠페인마진.py)용 설정 테이블 1개
-- AGENTS.md 규칙 9번: SQL 파일 → 설명 → DB 적용 → 코드 배포
--
-- 변경 내용: 새 테이블 1개를 "추가"만 합니다. 기존 테이블·데이터는 건드리지 않습니다.
--
-- 왜 필요한가:
--   '애초에 목표한 콘텐츠 개수가 채워졌는지'를 보려면 캠페인마다 목표 개수가 있어야 하는데,
--   지금 DB(sales_campaigns 등) 어디에도 목표 개수 칸이 없습니다.
--   직원이 쓰는 테이블(sales_campaigns)을 건드리지 않고, 재무 쪽에서 따로 적을 수 있는 설정 테이블을 둡니다.
--   (나중에 김선재가 팀스페이스 캠페인 등록 화면에서 같은 값을 입력하게 하면 이 테이블을 그대로 쓰면 됩니다.)
--
-- 칸 설명:
--   campaign_id           sales_campaigns.id (캠페인 1개당 1줄)
--   target_content_count  이 캠페인에서 올리기로 한 콘텐츠 개수(비어 있으면 화면에 '목표 미입력')
--   target_margin         이 캠페인만 다른 목표 마진율을 쓰고 싶을 때(0.65 = 65%). 비우면 기본 65%
--   note / updated_by / updated_at  메모·입력자·수정시각
--
-- 보안: RLS 켬(정책 없음). 서비스 키로만 접근.
-- 되돌리기: DROP TABLE public.campaign_margin_settings;
-- 참고: 이 테이블이 없어도 Campaign Margin 화면은 열리고, 목표 개수만 '입력 대기'로 보입니다.

CREATE TABLE IF NOT EXISTS public.campaign_margin_settings (
  campaign_id          uuid PRIMARY KEY REFERENCES public.sales_campaigns(id) ON DELETE CASCADE,
  target_content_count int  CHECK (target_content_count IS NULL OR target_content_count >= 0),
  target_margin        numeric CHECK (target_margin IS NULL OR (target_margin >= 0 AND target_margin < 1)),
  note                 text,
  updated_by           text,
  updated_at           timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.campaign_margin_settings ENABLE ROW LEVEL SECURITY;
