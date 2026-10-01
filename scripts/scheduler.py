"""
Railway Cron 카드 1개로 4개 작업(완성도체크·아침체크인·오후마무리·KPI정렬)을
전부 돌리는 통합 디스패처.

원리: 10분마다 깨어나서(Railway Cron이 호출), "지금 몇 시 몇 분인지" 보고
그 시간에 맞는 작업만 골라서 실행합니다. 매번 깨어날 때마다 완성도체크는
항상 돌리고, 아침 9시대/오후 4시대/KPI정렬(9시·18시대)은 그 시간에만 돕니다.

Railway 설정:
  Cron Schedule: */10 9-18 * * 1-5   (평일 9~18시, 10분마다)
  Start Command: python scripts/scheduler.py
  Variables: 아래 스크립트들이 각자 필요로 하는 환경변수를 전부 이 서비스 하나에 등록
             (SUPABASE_URL, SUPABASE_SERVICE_KEY, RESEND_API_KEY, RESEND_FROM,
              DAILY_REPORT_APP_URL, ANTHROPIC_API_KEY, 선택: GOOGLE_SERVICE_ACCOUNT_JSON, DRIVE_FOLDER_ID)

⚠️ 주의: 이 파일과 같은 scripts/ 폴더 안에 아래 4개 파일이 "정확히 이 이름"으로 있어야
   import가 됩니다 (띄어쓰기나 대문자가 섞이면 import 자체가 실패합니다):
   - completeness_check.py
   - kpi_context_sync.py   ← 지금 'Kpi context sync.py'로 되어있으면 이 이름으로 바꿔주세요!
   - send_morning_checkin.py
   - send_afternoon_wrapup.py
"""

import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KST = timezone(timedelta(hours=9))


def now_kst():
    return datetime.now(KST)


def safe_run(label, fn):
    try:
        fn()
        print(f"  ✅ {label} 완료")
    except Exception as e:
        print(f"  ❌ {label} 실패: {e}")


def main():
    now = now_kst()
    h, m = now.hour, now.minute
    print(f"[{now.isoformat()}] 스케줄러 시작 (KST {h:02d}:{m:02d})")

    # 완성도 체크 — 깨어날 때마다 항상 (10분 간격 호출이면 사실상 상시 체크)
    import completeness_check
    safe_run("completeness_check", completeness_check.main)

    # 아침 9시대 (09:00~09:09)
    if h == 9 and m < 10:
        import send_morning_checkin
        safe_run("send_morning_checkin", send_morning_checkin.main)

    # 오후 4시대 (16:00~16:09)
    if h == 16 and m < 10:
        import send_afternoon_wrapup
        safe_run("send_afternoon_wrapup", send_afternoon_wrapup.main)

    # KPI 맥락 정렬 — 하루 2번(9시대, 18시대)
    if (h == 9 or h == 18) and m < 10:
        import kpi_context_sync
        safe_run("kpi_context_sync", kpi_context_sync.main)

    print(f"[{now_kst().isoformat()}] 스케줄러 종료")


if __name__ == "__main__":
    main()
