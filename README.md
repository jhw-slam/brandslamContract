# 브랜드슬램 계약현황

Streamlit을 기반으로 한 브랜드슬램 계약 현황 실시간 모니터링 대시보드입니다.

## 기능

- 🔐 **비밀번호 보호**: 환경변수 기반의 간단한 인증
- 📊 **실시간 계약 현황 조회**: Supabase 데이터베이스 연동
- 📈 **데이터 분석**: Pandas를 이용한 데이터 처리
- 🎯 **상태 분류**: 결제 대기, 진행중, 완료 등 계약 상태 분류

## 설치

### 1. 프로젝트 클론
```bash
git clone https://github.com/jhw-slam/brandslamContract.git
cd brandslamContract
```

### 2. Python 환경 설정
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

### 3. 패키지 설치
```bash
pip install -r requirements.txt
```

### 4. 환경 변수 설정
`.env.example`을 참고하여 `.env` 파일을 생성합니다:

```bash
cp .env.example .env
```

`.env` 파일에 다음 정보를 입력합니다:
- `SUPABASE_URL`: Supabase 프로젝트 URL
- `SUPABASE_SERVICE_KEY`: Supabase 서비스 롤 키
- `APP_PASSWORD`: 애플리케이션 접근 비밀번호

## 실행

```bash
streamlit run streamlit_app.py
```

그 후 브라우저에서 `http://localhost:8501`로 접속합니다.

## 파일 구조

```
brandslamContract/
├── README.md                 # 프로젝트 설명
├── requirements.txt          # Python 의존성
├── .env.example             # 환경 변수 템플릿
├── .gitignore               # Git 제외 파일
└── streamlit_app.py         # Streamlit 메인 애플리케이션
```

## 필수 환경 변수

| 변수명 | 설명 |
|--------|------|
| `SUPABASE_URL` | Supabase 프로젝트 URL |
| `SUPABASE_SERVICE_KEY` | Supabase 서비스 롤 API 키 |
| `APP_PASSWORD` | 애플리케이션 접근 비밀번호 |

## 요구사항

- Python 3.8 이상
- Streamlit >= 1.36
- Supabase Python SDK >= 2.6
- Pandas >= 2.0

## 라이선스

MIT
