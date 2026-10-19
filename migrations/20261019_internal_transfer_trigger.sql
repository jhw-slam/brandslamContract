-- '브랜드슬램'(우리 회사 이름)이 적힌 거래를 DB에 들어올 때부터 자동으로 '계좌간 자금이동(내부이체)'로 분류합니다.
-- AGENTS.md 규칙 9번: SQL 파일 → 설명 → DB 적용 → 코드 배포
--
-- 현재 문제(2026-10-19 점검):
--   기존 트리거 trg_auto_tag_internal_transfer 는 적요가 '주식회사브랜드슬램'·'(주)브랜드슬램'·'브랜드슬램'과 "정확히 같을 때만" 분류합니다.
--   그래서 은행이 이름을 줄여 주거나 앞뒤에 글자를 붙인 표기('기업－（주）브랜드슬', '（주）브랜드슬램'(전각 괄호),
--   '주식회사브랜드슬램 IB (용산)', '브랜드슬램sc' …)는 분류되지 않았습니다. (미분류 입금 6건 ₩17,222,500 · 미분류 출금 37건 ₩44,800,000)
--   또 20만 원 미만 출금은 '소액 자동분류' 트리거가 먼저 실행돼서(트리거는 이름순으로 실행) 이 규칙이 적용되지 않았습니다(7건 ₩1,080,000).
--
-- 변경 내용(함수 1개 교체 + 트리거 이름·실행 순서 변경. 테이블·데이터는 바뀌지 않습니다):
--   ① 전각 괄호·하이픈·공백을 정리한 뒤 적요에 '브랜드슬'이 들어 있으면 내부이체(TRANSFER_INTERNAL)로 분류(입금·출금 모두)
--   ② 미분류이거나 '소액 자동분류'로 들어간 거래만 바꿉니다. 대표님이 직접 다른 계정과목으로 확정한 거래는 건드리지 않습니다.
--   ③ 트리거를 trg_000_… 이름으로 바꿔 다른 자동분류 트리거보다 먼저 실행되게 합니다.
-- 이미 들어 있는 50건은 이 파일로 바뀌지 않습니다 → 별도 파일 20261019_internal_transfer_backfill.sql (대상 건수 확인 후 적용)
--
-- 되돌리기: 트리거를 다시 trg_auto_tag_internal_transfer 로 만들고 함수를 옛 정의(정확히 같을 때만)로 되돌리면 됩니다.
-- 참고: 이 적용 전에도 재무 화면은 같은 규칙을 화면에서 적용해 숫자에 반영합니다(DB는 그대로).

CREATE OR REPLACE FUNCTION public.fn_auto_tag_internal_transfer()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
declare
  transfer_cat_id uuid;
  clean_desc text;
begin
  if new.description is not null and (new.account_category_id is null or coalesce(new.is_auto_categorized, false)) then
    clean_desc := regexp_replace(translate(new.description, '（）－　', '()- '), '\s+', '', 'g');
    if position('브랜드슬' in clean_desc) > 0 then
      select id into transfer_cat_id from public.fin_account_categories where code = 'TRANSFER_INTERNAL' limit 1;
      if transfer_cat_id is not null then
        new.account_category_id := transfer_cat_id;
        new.is_auto_categorized := false;
      end if;
    end if;
  end if;
  return new;
end;
$function$;

DROP TRIGGER IF EXISTS trg_auto_tag_internal_transfer ON public.bank_transactions;
DROP TRIGGER IF EXISTS trg_000_auto_tag_internal_transfer ON public.bank_transactions;
CREATE TRIGGER trg_000_auto_tag_internal_transfer
  BEFORE INSERT OR UPDATE ON public.bank_transactions
  FOR EACH ROW EXECUTE FUNCTION public.fn_auto_tag_internal_transfer();
