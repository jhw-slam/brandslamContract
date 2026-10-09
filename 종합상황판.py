"""앱 시작 파일(Procfile: streamlit run 종합상황판.py) — 상단 메뉴(라우터).

대표님이 자주 여는 핵심 화면 3그룹만 보여준다. 나머지 페이지는 삭제하지 않고 메뉴에서만 숨겼다.
숨긴 페이지를 다시 보려면 Railway 변수 SHOW_LEGACY_PAGES=1 을 추가하면 'Archive' 메뉴가 나타난다.
(st.navigation 을 쓰면 pages/ 폴더의 자동 목록은 꺼지고, 여기에 적은 페이지만 메뉴에 나온다.)
각 페이지의 로그인(APP_PASSWORD 등)은 페이지 안에서 기존 그대로 동작한다.
"""
import os

import streamlit as st

st.set_page_config(page_title="브랜드슬램 대표 대시보드", layout="wide")

NAV = {
    "CEO · Decisions": [
        st.Page("pages/16_커맨드센터.py", title="Command Center", icon="🎯", url_path="command-center", default=True),
        st.Page("pages/11_회의록.py", title="Meetings", icon="🗒️", url_path="meetings"),
    ],
    "CFO · Finance & IR": [
        st.Page("pages/17_수금관리.py", title="Receivables", icon="💰", url_path="receivables"),
        st.Page("pages/9_재무캘린더.py", title="Finance Calendar", icon="💹", url_path="finance"),
    ],
    "COO · People & Automation": [
        st.Page("pages/14_ OKR피드백관리.py", title="OKR Feedback", icon="🧭", url_path="okr-feedback"),
        st.Page("pages/15_휴가관리.py", title="Leave", icon="🏖️", url_path="leave"),
    ],
}

if os.environ.get("SHOW_LEGACY_PAGES") == "1":
    NAV["Archive (legacy)"] = [
        st.Page("legacy/종합상황판_구.py", title="종합상황판(구)", icon="📦", url_path="old-dashboard"),
        st.Page("pages/2_계약_콘솔.py", title="계약 콘솔", icon="📦", url_path="old-contract-console"),
        st.Page("pages/3_계약서_작성.py", title="계약서 작성", icon="📦", url_path="old-contract-write"),
        st.Page("pages/4_계약서_보관함.py", title="계약서 보관함", icon="📦", url_path="old-contract-vault"),
        st.Page("pages/5_송금캘린더.py", title="송금캘린더", icon="📦", url_path="old-remit-calendar"),
        st.Page("pages/6_인플루언서 인보이스.py", title="인플루언서 인보이스", icon="📦", url_path="old-influencer-invoice"),
        st.Page("pages/7_OKR_목표관리.py", title="OKR 목표관리", icon="📦", url_path="old-okr-manage"),
        st.Page("pages/8_영업.py", title="영업", icon="📦", url_path="old-sales"),
        st.Page("pages/10_손익현황.py", title="손익현황", icon="📦", url_path="old-pnl"),
        st.Page("pages/12_지출예정보고.py", title="지출예정보고", icon="📦", url_path="old-spend-report"),
        st.Page("pages/13_데일리업무보고.py", title="데일리업무보고", icon="📦", url_path="old-daily-report"),
    ]

st.navigation(NAV, position="top").run()
