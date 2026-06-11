import streamlit as st
from pathlib import Path

st.set_page_config(page_title="계약 콘솔", layout="wide")

st.title("계약 콘솔")

console_path = Path("console.html")
if not console_path.exists():
    st.error("console.html 파일이 없습니다. 프로젝트 루트에 console.html을 업로드해주세요.")
else:
    html = console_path.read_text(encoding="utf-8")
    st.components.v1.html(html, height=1200, scrolling=True)
