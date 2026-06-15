import os
import io
import re

import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client

st.set_page_config(page_title="계약서 보관함", layout="wide")

# ── 브랜드슬램(B) 기본정보 (디폴트 — 업로드 계약서 정보가 다르거나 과거값이어도 이걸 표준으로) ──
BRANDSLAM = {
    "name": "주식회사 브랜드슬램",
    "biz": "284-88-03016",
    "ceo": "장현우",
    "addr": "서울시 강남구 테헤란로 7길 11, 한덕빌딩 9층 902호",
}

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

# ── 텍스트 → 계약서 서식 HTML (문단/줄바꿈 유지) ──────────────
def text_to_html(txt):
    parts = [p for p in re.split(r"\n\s*\n", txt or "") if p.strip()]
    out = []
    for p in parts:
        esc = p.replace("<", "&lt;").replace(">", "&gt;").strip()
        # '제N조' 또는 '1.' 같은 조항 머리글은 소제목처럼
        if re.match(r"^(제?\s*\d+\s*조|\d+\.)\s", esc) or len(esc) < 40 and esc.endswith(":"):
            out.append(f"<h3>{esc}</h3>")
        else:
            out.append("<p>" + esc.replace("\n", "<br>") + "</p>")
    return "".join(out)

def extract(name, data):
    """returns (html_for_preview, plain_text_for_edit)"""
    n = name.lower()
    try:
        if n.endswith(".docx"):
            import mammoth
            html = mammoth.convert_to_html(io.BytesIO(data)).value
            text = mammoth.extract_raw_text(io.BytesIO(data)).value
            return html, text
        if n.endswith(".pdf"):
            from pypdf import PdfReader
            text = "\n".join((pg.extract_text() or "") for pg in PdfReader(io.BytesIO(data)).pages)
            return text_to_html(text), text
        text = data.decode("utf-8", "ignore")
        return text_to_html(text), text
    except Exception as e:
        return f"<p>(파일을 읽지 못했습니다: {e})</p>", ""

def guess_party(text, fallback):
    for pat in [r"Full Name[:：]\s*([^\n,]{2,40})", r"주식회사\s*([가-힣A-Za-z0-9 ]{1,20})",
                r"\(주\)\s*([가-힣A-Za-z0-9 ]{1,20})", r"대표이사[:：]?\s*([가-힣]{2,6})"]:
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

# ── A4 문서 서식 (제목/조항/표 스타일 유지, 여러 페이지 OK) ───
A4_CSS = (
    "*{box-sizing:border-box}body{background:#e9edf3;margin:0;"
    "font-family:'Apple SD Gothic Neo','Malgun Gothic','Noto Sans KR',sans-serif}"
    ".page{width:210mm;min-height:297mm;padding:24mm 22mm;margin:14px auto;background:#fff;"
    "box-shadow:0 3px 20px rgba(20,30,50,.18);color:#1a1a1a;line-height:1.85;font-size:11pt}"
    ".page h1{font-size:16pt;text-align:center;letter-spacing:2px;margin:0 0 22px}"
    ".page h2{font-size:12.5pt;margin:18px 0 8px;border-bottom:1px solid #e3e3e3;padding-bottom:4px}"
    ".page h3{font-size:11.5pt;margin:14px 0 6px;font-weight:700}"
    ".page p{margin:7px 0}.page strong{font-weight:700}"
    ".page table{border-collapse:collapse;width:100%;margin:10px 0;font-size:10.5pt}"
    ".page td,.page th{border:1px solid #bbb;padding:6px 8px;text-align:left}"
    ".page ul,.page ol{margin:6px 0 6px 18px}"
    "@media print{body{background:#fff}.page{box-shadow:none;margin:0}@page{size:A4;margin:0}}"
)
def wrap(inner):
    return ("<html><head><meta charset='utf-8'><style>" + A4_CSS +
            "</style></head><body><div class='page'>" + inner + "</div></body></html>")

def projects_for_select():
    projs = SUPA.table("projects").select("id,brand,product,company_id").order("created_at").execute().data
    comp = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    opts = {"(브랜드 선택 안 함)": None}
    for p in projs:
        opts[f"{comp.get(p['company_id'],'')} · {p.get('product') or p['brand']}"] = (p["id"], comp.get(p["company_id"], ""))
    return opts

# ── 화면 ──────────────────────────────────────────────────────
st.title("계약서 보관함")
st.caption("계약서 파일(PDF·DOCX·TXT)을 올리면 **서식을 유지한 채** 미리보고, 템플릿으로 재사용해 브랜드에 저장할 수 있어요. (구글드라이브 인증 불필요)")

with st.expander(f"브랜드슬램(B) 기본정보 — 표준값 (복사용)"):
    st.code(f"{BRANDSLAM['name']}\n사업자등록번호 {BRANDSLAM['biz']}\n대표이사 {BRANDSLAM['ceo']}\n주소: {BRANDSLAM['addr']}", language=None)
    st.caption("업로드한 과거 계약서에 회사정보가 다르게 적혀 있으면, 템플릿 편집 시 위 표준값으로 바꿔주세요.")

files = st.file_uploader("계약서 파일 업로드", type=["pdf", "docx", "txt"], accept_multiple_files=True)
if files:
    for f in files:
        data = f.getvalue()
        html, text = extract(f.name, data)
        party = guess_party(text, f.name.rsplit(".", 1)[0]); typ = guess_type(text)
        with st.expander(f"📄 {party}  ·  [{typ}]  ·  {f.name}", expanded=True):
            components.html(wrap(html), height=820, scrolling=True)
            if st.button("📋 이 계약서를 템플릿으로 사용하기", key="t" + f.name):
                st.session_state.tpl = {"party": party, "type": typ, "text": text, "html": html}
                st.rerun()

# ── 저장된 계약서 (Supabase contracts) ────────────────────────
st.divider()
st.subheader("저장된 계약서 (Supabase)")
saved = SUPA.table("contracts").select("*").order("created_at", desc=True).limit(50).execute().data
if saved:
    pj = SUPA.table("projects").select("id,brand,company_id").execute().data
    cm = {c["id"]: c["name"] for c in SUPA.table("companies").select("id,name").execute().data}
    pmap = {p["id"]: cm.get(p["company_id"], p["brand"]) for p in pj}
    for s in saved:
        cols = st.columns([6, 2])
        cols[0].write(f"· [{s.get('doc_type') or '-'}] {s.get('counterparty') or ''} · "
                      f"{pmap.get(s.get('project_id'), '미연결')} · {(s.get('created_at') or '')[:10]}")
        if s.get("body"):
            cols[1].download_button("내려받기", s["body"], file_name=f"계약서_{s.get('counterparty') or 'doc'}.html",
                                    mime="text/html", key="dl" + s["id"])
else:
    st.caption("· 아직 저장된 계약서가 없습니다. 위에서 업로드 후 '템플릿으로 사용' → 브랜드 저장하거나, '계약서 작성' 탭에서 저장하세요.")

# ── 템플릿 사용: 내용 수정 → 브랜드로 저장 (서식 유지) ────────
tpl = st.session_state.get("tpl")
if tpl:
    st.divider()
    st.subheader(f"템플릿 사용 — {tpl['party']} ({tpl['type']})")
    edited = st.text_area("계약 내용 수정 (머지값·금액·조건·날짜 등) — 회사정보는 위 표준값 참고", value=tpl["text"], height=420)
    st.markdown("**미리보기 (A4, 서식 유지)**")
    components.html(wrap(text_to_html(edited)), height=560, scrolling=True)
    opts = projects_for_select()
    c1, c2 = st.columns([2, 1])
    target_label = c1.selectbox("어느 브랜드 계약현황으로 저장할까요?", list(opts.keys()))
    target = opts[target_label]
    if c2.button("💾 이 브랜드로 저장", type="primary", use_container_width=True):
        SUPA.table("contracts").insert({
            "project_id": target[0] if target else None,
            "doc_type": "creator" if tpl["type"] == "인플루언서" else "brand",
            "counterparty": tpl["party"], "body": wrap(text_to_html(edited)), "sign_status": "draft",
        }).execute()
        st.session_state.pop("tpl", None)
        st.toast("Supabase에 저장되었습니다 ✓")
        st.success(f"저장 완료 — {target[1] if target else '브랜드 미연결'}. 콘솔/이 목록에서 확인하세요.")
        st.rerun()
    if st.button("템플릿 닫기"):
        st.session_state.pop("tpl", None); st.rerun()
