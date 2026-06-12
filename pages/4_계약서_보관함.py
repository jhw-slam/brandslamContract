import os

import streamlit as st

st.set_page_config(page_title="계약서 보관함", layout="wide")

PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

st.title("계약서 보관함")
st.info("구글 드라이브 연동은 준비 중입니다. (서비스 계정 또는 OAuth 설정 후 다시 활성화 예정)\n\n"
        "그동안 계약서는 '계약서 작성' 탭에서 만들고 브랜드에 저장하면, '계약 콘솔'의 해당 브랜드에서 확인할 수 있어요.")
