import os
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="브랜드슬램 계약 관리", layout="wide", initial_sidebar_state="expanded")

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

# ── 메인 페이지 ───────────────────────────────────────────────
st.title("🏢 브랜드슬램 계약 관리 시스템")
st.markdown("""
---
## 📌 시스템 개요

이 시스템은 **프로젝트 ID를 중심**으로 모든 데이터(계약서, 송금, 상태)를 연동합니다.

### 🔗 핵심 개념: **프로젝트 ID**

모든 정보는 **고유한 프로젝트 ID**로 연결되어 있습니다:

```
프로젝트 (고유 ID)
├─ 계약서 (contracts table) → project_id로 연결
├─ 송금 일정 (cash_events table) → project_id로 연결  
├─ 상태 (projects table) → stage, deposit_paid, balance_paid
└─ 실행사 (project_vendors table) → project_id로 연결
```

---

## 📖 각 페이지의 역할

### 1️⃣ **계약 콘솔** 🏠
- **역할**: 프로젝트의 중심 대시보드
- **기능**:
  - 계약 선택 (좌측 사이드바)
  - 진행 단계 관리 (LEAD → SIGNED → DEPOSIT → ... → SETTLED)
  - 수익 구조 (매출, 비용, 이윤)
  - 송금 일정 관리
  - **계약서 저장 및 열람** (고정값으로 저장됨)

### 2️⃣ **계약서 작성** ✏️
- **역할**: 계약서 생성 및 저장
- **기능**:
  - 새 계약서 템플릿 입력
  - 또는 **기존 계약서 불러오기** (보관함에서 저장된 것)
  - 프로젝트 선택해서 저장
  - 언제든 다시 수정 가능

### 3️⃣ **계약서 보관함** 📂
- **역할**: 계약서 파일 관리
- **기능**:
  - PDF/DOCX/TXT 파일 **수동 업로드**
  - 프로젝트 **자동 신설** 또는 기존 선택
  - **A4 서식 유지** 저장
  - 프로젝트별 그룹화된 목록
  - 필요할 때 **언제든 열람 가능** (고정값)

### 4️⃣ **송금 캘린더** 💰
- **역할**: 송금 일정 관리
- **기능**:
  - **프로젝트별 필터링** (프로젝트 ID로)
  - 표/달력 두 가지 보기
  - **각 송금 건별로 프로젝트 선택** (다중 가능)
  - **인라인 수정** (표에서 바로 프로젝트 변경)
  - 상세 수정 폼

---

## 🔄 데이터 연동 방식

### ✅ **올바른 사용법**

#### Step 1: 프로젝트 생성
```
계약 콘솔 좌측 → "계약 추가" 
  ↓
새 프로젝트 생성 (자동으로 고유 ID 발급)
```

#### Step 2: 계약서 저장
```
계약서 보관함 → 파일 업로드
  ↓
프로젝트 선택 또는 신설
  ↓
저장 (프로젝트 ID와 함께 contracts 테이블에 저장)
  ↓
계약 콘솔에서 언제든 열람 가능 ✓
```

#### Step 3: 송금 관리
```
송금 캘린더 → "송금 일정 추가"
  ↓
프로젝트 ID로 선택 ⭐
  ↓
저장 (cash_events 테이블에 project_id와 함께 저장)
  ↓
같은 프로젝트의 모든 송금이 계약 콘솔에서 표시됨 ✓
```

---

## ⚠️ 데이터 연동 문제 해결

### 문제: "송금이 콘솔에 안 보여요"
**원인**: 송금과 프로젝트의 **프로젝트 ID가 다르거나 연결되지 않음**

**해결**:
1. 계약 콘솔에서 프로젝트 선택
2. 프로젝트 ID 확인 (예: `abc123def`)
3. 송금 캘린더에서 **같은 프로젝트 ID** 선택해서 송금 추가
4. 자동으로 계약 콘솔에 표시됨

### 문제: "어떤 프로젝트와 연결되어 있는지 모르겠어요"
**해결**:
- 각 페이지에 프로젝트 ID 표시됨
- 계약 콘솔: 상단에 "📌 프로젝트 ID: `xxx`" 표시
- 송금 캘린더: 각 송금 건의 프로젝트명 표시, 클릭하면 프로젝트 ID 확인

---

## 📊 데이터 구조 (Supabase)

| 테이블 | 주요 칼럼 | 역할 |
|--------|---------|------|
| **projects** | id, brand, company_id, stage, supply_amount | 프로젝트 기본 정보 |
| **contracts** | id, project_id, doc_type, body, sign_status | 계약서 저장 (project_id로 연결) |
| **cash_events** | id, project_id, direction, amount, due_date | 송금 일정 (project_id로 연결) |
| **companies** | id, name | 업체 정보 |
| **project_vendors** | id, project_id, vendor_name, amount | 실행사 (project_id로 연결) |

---

## ✨ 주요 개선사항

✅ **수동 업로드**: Google Drive 제거, 파일 직접 업로드  
✅ **프로젝트 ID 기반 연동**: 모든 데이터가 고유 ID로 연결  
✅ **다중 송금**: 여러 송금 건이 같은 프로젝트와 연결 가능  
✅ **인라인 수정**: 표에서 바로 프로젝트 변경  
✅ **달력 상세보기**: 날짜 클릭해서 송금 상세 확인  
✅ **고정값 저장**: 계약서는 한 번 저장하면 필요할 때 언제든 열람 가능  

---

## 🚀 빠른 시작

### 새 프로젝트 추가
1. 좌측 "계약 추가" 클릭
2. 업체명, 상품명 입력
3. 단계 선택

### 계약서 저장
1. "계약서 보관함" 탭
2. 파일 업로드
3. 프로젝트 선택
4. "저장" 클릭

### 송금 추가
1. "송금 캘린더" 탭
2. "송금 일정 추가"
3. **프로젝트 ID로 선택** ⭐
4. 저장

---

**문제가 있으면 각 페이지의 💡 팁을 확인하세요!**
""")

# ── 현황 요약 ─────────────────────────────────────────────────
st.divider()
st.markdown("## 📈 현재 현황")

try:
    projs = SUPA.table("projects").select("*").execute().data
    contracts = SUPA.table("contracts").select("id").execute().data
    cash_events = SUPA.table("cash_events").select("id,amount,direction").execute().data
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("🏢 총 프로젝트", len(projs))
    col2.metric("📄 저장된 계약서", len(contracts))
    col3.metric("💰 총 송금 건", len(cash_events))
    
    # 송금 통계
    total_in = sum(e.get("amount", 0) for e in cash_events if e.get("direction") == "in")
    col4.metric("받을 돈 (총합)", f"₩{total_in:,}")
    
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")

