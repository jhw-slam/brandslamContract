import os
import io
import re
import json
from datetime import datetime

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

def load_projects():
    return SUPA.table("projects").select("*").order("created_at").execute().data

def load_companies():
    return SUPA.table("companies").select("id,name").execute().data

def get_or_create_company(name):
    """회사 ID를 얻거나 없으면 생성"""
    name = (name or "(미상)").strip()
    r = SUPA.table("companies").select("id").eq("name", name).limit(1).execute().data
    if r:
        return r[0]["id"]
    return SUPA.table("companies").insert({"name": name}).execute().data[0]["id"]

def create_project(company_name, product_name, contract_type):
    """프로젝트 생성"""
    company_id = get_or_create_company(company_name)
    proj = SUPA.table("projects").insert({
        "company_id": company_id,
        "brand": company_name,
        "product": product_name,
        "campaign": product_name,
        "stage": "SENT",  # 계약서 발송 단계부터 시작
        "supply_amount": 0,
        "total_amount": 0,
        "doc_type": contract_type,
    }).execute().data
    return proj[0] if proj else None

# ── 텍스트 → 계약서 서식 HTML (문단/줄바꿈 유지) ──────────────
def text_to_html(txt):
    """텍스트를 HTML 서식으로 변환 (조항 구조 유지)"""
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

def size_images(html):
    """docx 안 이미지를 점검: 정사각(도장/직인)은 2.2cm로 축소, 그 외는 본문폭 제한(CSS)."""
    import base64
    try:
        from PIL import Image
    except Exception:
        return html

    def repl(m):
        tag = m.group(0); b64 = m.group(1)
        try:
            im = Image.open(io.BytesIO(base64.b64decode(b64))); w, h = im.size
        except Exception:
            return tag
        if h and 0.6 <= (w / h) <= 1.6 and "class=" not in tag:
            return "<img class=\"stamp\"" + tag[4:]
        return tag
    return re.sub(r'<img\b[^>]*src="data:image/[^;]+;base64,([^"]+)"[^>]*>', repl, html)

def extract(name, data):
    """파일에서 HTML과 텍스트 추출 (docx, pdf, txt 지원)"""
    n = name.lower()
    try:
        if n.endswith(".docx"):
            import mammoth
            html = size_images(mammoth.convert_to_html(io.BytesIO(data)).value)
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
    """계약서 텍스트에서 거래 상대방 이름 추출"""
    for pat in [r"Full Name[:：]\s*([^\n,]{2,40})", r"주식회사\s*([가-힣A-Za-z0-9 ]{1,20})",
                r"\(주\)\s*([가-힣A-Za-z0-9 ]{1,20})", r"대표이사[:：]?\s*([가-힣]{2,6})"]:
        m = re.search(pat, text or "")
        if m:
            return m.group(1).strip()
    return fallback

def guess_type(text):
    """계약서 텍스트에서 계약 유형 추측 (인플루언서 vs 브랜드사)"""
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
    ".page img{max-width:100%;height:auto}"
    ".page img.stamp{width:2.2cm;height:2.2cm;object-fit:contain;vertical-align:middle}"
    "@media print{body{background:#fff}.page{box-shadow:none;margin:0}@page{size:A4;margin:0}}"
)

def wrap(inner):
    """HTML을 A4 페이지 서식으로 감싸기"""
    return ("<html><head><meta charset='utf-8'><style>" + A4_CSS +
            "</style></head><body><div class='page'>" + inner + "</div></body></html>")

def create_contract_record(project_id, doc_type, counterparty, body, html):
    """계약서를 Supabase에 저장"""
    return SUPA.table("contracts").insert({
        "project_id": project_id,
        "doc_type": doc_type,
        "counterparty": counterparty,
        "body": wrap(html),
        "sign_status": "draft",
        "uploaded_at": datetime.now().isoformat(),
    }).execute().data

# ── 화면 ──────────────────────────────────────────────────────
st.title("계약서 보관함")
st.caption("💡 계약서 파일(PDF·DOCX·TXT)을 업로드하면 **서식을 유지한 채** 미리보고, 브랜드 프로젝트에 **고정값으로 저장**할 수 있습니다. (구글드라이브 인증 불필요)")

with st.expander("📋 브랜드슬램(B) 기본정보 — 표준값"):
    st.code(f"{BRANDSLAM['name']}\n사업자등록번호 {BRANDSLAM['biz']}\n대표이사 {BRANDSLAM['ceo']}\n주소: {BRANDSLAM['addr']}", language=None)
    st.caption("업로드한 과거 계약서에 회사정보가 다르면, 아래 편집할 때 위 표준값으로 바꿔주세요.")

st.divider()
st.subheader("📁 계약서 업로드 및 프로젝트 저장")

files = st.file_uploader("계약서 파일 선택 (PDF, DOCX, TXT)", type=["pdf", "docx", "txt"], accept_multiple_files=True)

if files:
    for f in files:
        st.info(f"📄 파일: **{f.name}**")
        data = f.getvalue()
        html, text = extract(f.name, data)
        party = guess_party(text, f.name.rsplit(".", 1)[0]); typ = guess_type(text)
        
        # 미리보기
        with st.expander(f"📖 미리보기 — [{typ}] {party}", expanded=False):
            components.html(wrap(html), height=600, scrolling=True)
        
        # 프로젝트 선택/생성 UI
        st.subheader(f"🔗 {party} 계약서 저장")
        
        col1, col2 = st.columns(2)
        with col1:
            action = st.radio("선택", ["기존 프로젝트에 저장", "새 프로젝트 생성"], 
                             key=f"action_{f.name}", horizontal=True)
        
        if action == "기존 프로젝트에 저장":
            projects = load_projects()
            companies = load_companies()
            comp_map = {c["id"]: c["name"] for c in companies}
            
            opts = {}
            for p in projects:
                label = f"{comp_map.get(p['company_id'], '')} · {p.get('product') or p['brand']}"
                opts[label] = p
            
            if opts:
                selected_label = st.selectbox(f"프로젝트 선택", list(opts.keys()), key=f"proj_{f.name}")
                selected_proj = opts[selected_label]
                
                if st.button(f"💾 '{selected_label}'에 저장", type="primary", key=f"save_{f.name}"):
                    create_contract_record(
                        selected_proj["id"],
                        doc_type=typ,
                        counterparty=party,
                        body=text,
                        html=html
                    )
                    st.success(f"✅ 계약서가 저장되었습니다!")
            else:
                st.warning("등록된 프로젝트가 없습니다. 새 프로젝트를 생성하세요.")
        
        else:  # 새 프로젝트 생성
            st.markdown("**새 프로젝트 정보 입력**")
            ncol1, ncol2 = st.columns(2)
            with ncol1:
                new_company = st.text_input(f"업체명", value=party, key=f"comp_{f.name}")
            with ncol2:
                new_product = st.text_input(f"상품/캠페인명", key=f"prod_{f.name}")
            
            if st.button(f"➕ 새 프로젝트 생성 & 저장", type="primary", key=f"create_{f.name}"):
                if new_company:
                    new_proj = create_project(new_company, new_product or "미지정", typ)
                    if new_proj:
                        create_contract_record(
                            new_proj["id"],
                            doc_type=typ,
                            counterparty=party,
                            body=text,
                            html=html
                        )
                        st.success(f"✅ 새 프로젝트가 생성되고 계약서가 저장되었습니다!")
                        st.info(f"📌 프로젝트 ID: `{new_proj['id']}` | 업체: {new_company}")
                else:
                    st.error("업체명을 입력하세요.")
        
        st.divider()

# ── 저장된 계약서 목록 ────────────────────────────────────────
st.subheader("📚 저장된 계약서")

projects = load_projects()
companies = load_companies()
comp_map = {c["id"]: c["name"] for c in companies}

saved = SUPA.table("contracts").select("*").order("created_at", desc=True).limit(100).execute().data

if saved:
    # 프로젝트별로 그룹화
    grouped = {}
    for s in saved:
        pid = s.get("project_id")
        proj = next((p for p in projects if p["id"] == pid), None)
        if proj:
            proj_label = f"{comp_map.get(proj['company_id'], '')} · {proj.get('product') or proj['brand']}"
        else:
            proj_label = "(미연결)"
        
        if proj_label not in grouped:
            grouped[proj_label] = []
        grouped[proj_label].append(s)
    
    # UI 표시
    for proj_label, contracts in grouped.items():
        with st.expander(f"📌 {proj_label} — {len(contracts)}건", expanded=False):
            for idx, s in enumerate(contracts):
                cols = st.columns([6, 1.5, 1])
                cols[0].write(f"• [{s.get('doc_type') or '-'}] {s.get('counterparty') or ''} · {(s.get('created_at') or '')[:10]}")
                
                if s.get("body"):
                    cols[1].download_button(
                        "📄 내려받기",
                        s["body"],
                        file_name=f"계약서_{s.get('counterparty', 'doc')}_{s.get('created_at', '')[:10]}.html",
                        mime="text/html",
                        key=f"dl_{s['id']}"
                    )
                
                # 삭제 버튼
                if cols[2].button("🗑️", key=f"del_{s['id']}", help="삭제"):
                    SUPA.table("contracts").delete().eq("id", s["id"]).execute()
                    st.rerun()
else:
    st.info("아직 저장된 계약서가 없습니다. 위에서 파일을 업로드하고 프로젝트에 저장하세요.")
