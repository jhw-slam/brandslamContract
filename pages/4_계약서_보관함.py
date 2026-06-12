import os
import io
import json
import re

import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client

st.set_page_config(page_title="계약서 보관함", layout="wide")

# ── 비밀번호 게이트 ───────────────────────────────────────────
PW = os.environ.get("APP_PASSWORD")
if PW and not st.session_state.get("ok"):
    pw = st.text_input("비밀번호", type="password")
    if st.button("입장"):
        if pw == PW:
            st.session_state.ok = True; st.rerun()
        else:
            st.error("비밀번호가 올바르지 않습니다.")
    st.stop()

@st.cache_resource
def sb():
    url = os.environ.get("SUPABASE_URL"); key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        st.error("❌ SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수가 없습니다."); st.stop()
    return create_client(url, key)
SUPA = sb()

DRIVE_FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID", "1hpPLwG5AoykCve0ouUB3tYY7PdfJMGss")

# ── Google Drive (OAuth refresh token 방식) ───────────────────
@st.cache_resource
def drive():
    cid = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    csec = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    rtok = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    if not (cid and csec and rtok):
        return None
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=None, refresh_token=rtok,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=cid, client_secret=csec,
        scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)

DOC_MIMES = ("application/vnd.google-apps.document", "application/pdf",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
FOLDER_MIME = "application/vnd.google-apps.folder"
MARKERS = ["계약서", "Agreement", "대표이사", "갑", "을", "Creator", "CEO", "홍보 대행", "Campaign Agreement", "계약 당사자"]

def is_contract(text):
    if not text:
        return False
    if "계약서" in text or "Agreement" in text:
        return True
    return sum(1 for m in MARKERS if m in text) >= 2

def guess_party(text, fallback):
    for pat in [r"Full Name[:：]\s*([^\n,]{2,40})",
                r"주식회사\s*([가-힣A-Za-z0-9 ]{1,20})",
                r"\(주\)\s*([가-힣A-Za-z0-9 ]{1,20})",
                r"대표이사[:：]?\s*([가-힣]{2,6})"]:
        m = re.search(pat, text or "")
        if m:
            return m.group(1).strip()
    return fallback

def guess_type(text):
    t = text or ""
    if "Creator" in t or "크리에이터" in t or "Campaign Agreement" in t:
        return "인플루언서"
    if "홍보 대행" in t or ("갑" in t and "을" in t):
        return "브랜드사"
    return "기타"

def extract_text(svc, fid, mime):
    from googleapiclient.http import MediaIoBaseDownload
    try:
        if mime == "application/vnd.google-apps.document":
            data = svc.files().export(fileId=fid, mimeType="text/plain").execute()
            return data.decode("utf-8", "ignore") if isinstance(data, bytes) else str(data)
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        if mime == "application/pdf":
            from pypdf import PdfReader
            return "\n".join((pg.extract_text() or "") for pg in PdfReader(buf).pages)
        import docx
        return "\n".join(p.text for p in docx.Document(buf).paragraphs)
    except Exception:
        return ""

@st.cache_data(ttl=300)
def scan(folder_id):
    svc = drive()
    if svc is None:
        return None

    def list_children(fid):
        out, tok = [], None
        while True:
            r = svc.files().list(q=f"'{fid}' in parents and trashed=false",
                                  fields="nextPageToken, files(id,name,mimeType,webViewLink)",
                                  pageToken=tok, pageSize=100).execute()
            out += r.get("files", []); tok = r.get("nextPageToken")
            if not tok:
                break
        return out

    items = list_children(folder_id)
    files = []
    for it in items:
        if it["mimeType"] == FOLDER_MIME:
            files += [f for f in list_children(it["id"]) if f["mimeType"] != FOLDER_MIME]
        else:
            files.append(it)

    docs = []
    for f in files:
        if f["mimeType"] not in DOC_MIMES:
            continue
        text = extract_text(svc, f["id"], f["mimeType"])
        if not is_contract(text):
            continue
        docs.append({"id": f["id"], "name": f["name"], "url": f.get("webViewLink"),
                     "party": guess_party(text, f["name"]), "type": guess_type(text), "text": text})
    return docs

A4_CSS = ("*{box-sizing:border-box}body{background:#e9edf3;margin:0;"
          "font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif}"
          ".page{width:210mm;min-height:200mm;padding:24mm 22mm;margin:12px auto;background:#fff;"
          "box-shadow:0 3px 18px rgba(20,30,50,.18);color:#1a1a1a;line-height:1.8;font-size:11pt;"
          "white-space:pre-wrap}")

def a4(text):
    return ("<html><head><meta charset='utf-8'><style>" + A4_CSS +
            "</style></head><body><div class='page'>" + (text or "").replace("<", "&lt;") + "</div></body></html>")

# ── 화면 ──────────────────────────────────────────────────────
st.title("계약서 보관함")
svc = drive()
if svc is None:
    st.error("❌ Google OAuth 환경변수가 없습니다. Railway에 GOOGLE_OAUTH_CLIENT_ID / "
             "GOOGLE_OAUTH_CLIENT_SECRET / GOOGLE_OAUTH_REFRESH_TOKEN 3개를 넣어주세요.")
    st.stop()

cols = st.columns([3, 1])
type_filter = cols[0].radio("종류", ["전체", "브랜드사", "인플루언서"], horizontal=True)
if cols[1].button("🔄 새로고침"):
    st.cache_data.clear(); st.rerun()

docs = scan(DRIVE_FOLDER_ID) or []
view = [d for d in docs if type_filter == "전체" or d["type"] == type_filter]
st.caption(f"계약서 {len(view)}건 (드라이브 폴더에서 내용이 계약서인 파일만)")

for d in view:
    with st.expander(f"📄 {d['party']}  ·  [{d['type']}]  ·  {d['name']}"):
        components.html(a4(d["text"][:8000]), height=520, scrolling=True)
        if d.get("url"):
            st.markdown(f"[원본 문서 열기]({d['url']})")
        if st.button("📋 이 계약서를 템플릿으로 사용하기", key="t" + d["id"]):
            st.session_state.tpl = {"party": d["party"], "type": d["type"], "text": d["text"]}
            st.rerun()

# ── 템플릿으로 사용: 머지(내용) 수정 → 브랜드로 저장 ──────────
tpl = st.session_state.get("tpl")
if tpl:
    st.divider()
    st.subheader(f"템플릿 사용 — {tpl['party']} ({tpl['type']})")
    edited = st.text_area("계약 내용 수정 (머지값·금액·조건·날짜 등)", value=tpl["text"], height=420)

    projects = SUPA.table("projects").select("id,brand,product,company_id").order("created_at").execute().data
    cmap = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    opts = {"(브랜드 선택 안 함)": None}
    for p in projects:
        opts[f"{cmap.get(p['company_id'],'')} · {p.get('product') or p['brand']}"] = p

    c1, c2 = st.columns([2, 1])
    target_label = c1.selectbox("어느 브랜드 계약현황으로 보낼까요?", list(opts.keys()))
    target = opts[target_label]
    if c2.button("💾 이 브랜드로 저장", type="primary", use_container_width=True):
        SUPA.table("contracts").insert({
            "project_id": target["id"] if target else None,
            "doc_type": "creator" if tpl["type"] == "인플루언서" else "brand",
            "counterparty": tpl["party"],
            "body": a4(edited),
            "sign_status": "draft",
        }).execute()
        st.session_state.pop("tpl", None)
        if target:
            st.success(f"저장 완료 — '{cmap.get(target['company_id'],'')}' 계약현황(콘솔)에서 확인할 수 있어요.")
            st.rerun()
        else:
            st.success("저장 완료 (브랜드 미연결).")
    if st.button("템플릿 닫기"):
        st.session_state.pop("tpl", None); st.rerun()
