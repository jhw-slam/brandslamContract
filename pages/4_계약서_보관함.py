"""
구글 드라이브 읽기용 refresh token 받기 (로컬에서 1회 실행)

준비:
  pip install google-auth-oauthlib
  아래 CLIENT_ID / CLIENT_SECRET 를 본인 OAuth 클라이언트("채굴자동화") 값으로 채우기
  python get_refresh_token.py  →  브라우저에서 본인 구글 계정으로 동의

실행하면 맨 아래에 Railway 에 넣을 값 3개가 출력됩니다.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

# ↓↓↓ 두 값만 채우세요 ↓↓↓
CLIENT_ID = "849370409221-mickgnne1jbtqkl9uaqsht1q6ccvb3pj.apps.googleusercontent.com"
CLIENT_SECRET = "여기에_클라이언트_보안_비밀_GOCSPX-..._붙여넣기"
# ↑↑↑ 두 값만 채우세요 ↑↑↑

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

config = {
    "installed": {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}

flow = InstalledAppFlow.from_client_config(config, scopes=SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

print("\n========== Railway 환경변수에 넣을 값 ==========")
print("GOOGLE_OAUTH_CLIENT_ID =", creds.client_id)
print("GOOGLE_OAUTH_CLIENT_SECRET =", creds.client_secret)
print("GOOGLE_OAUTH_REFRESH_TOKEN =", creds.refresh_token)
print("===============================================")
if not creds.refresh_token:
    print("\n⚠ refresh_token 이 비어있으면, 계정 보안설정에서 이 앱 접근을 해제 후 다시 실행하세요.")
