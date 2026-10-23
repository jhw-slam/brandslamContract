"""세무사 공유 폴더(구글드라이브)에 자료를 올리고 목록을 읽는 작은 도구. streamlit 에 의존하지 않는다.

· 서비스계정(GOOGLE_SERVICE_ACCOUNT_JSON)으로 접속한다. 폴더에서 '편집자' 권한이 있어야 올릴 수 있다(열람만이면 목록만 보임).
· 개인 드라이브(내 드라이브) 폴더에는 서비스계정이 파일을 만들지 못하는 경우가 있다(저장 용량 0 → storageQuotaExceeded).
  그때는 '공유 드라이브'로 옮기거나 대표 계정(OAuth)으로 올리는 방식이 필요하다 — diagnose()가 이유를 알려준다.
· 비밀값은 코드에 두지 않는다(환경변수만 읽는다).
"""
import io
import json
import os
import re

SCOPE_FULL = "https://www.googleapis.com/auth/drive"
FOLDER_MIME = "application/vnd.google-apps.folder"


def folder_id_from_text(text):
    """폴더 주소(https://drive.google.com/drive/folders/ID?...) 또는 ID 그대로를 받아 ID를 돌려준다."""
    t = (text or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_\-]{10,})", t)
    if m:
        return m.group(1)
    return t if re.fullmatch(r"[A-Za-z0-9_\-]{10,}", t) else None


def configured_folder_id():
    """환경변수에서 세무사 공유 폴더 ID. TAX_DRIVE_FOLDER_ID 가 있으면 그것, 없으면 DRIVE_FOLDER_ID."""
    for name in ("TAX_DRIVE_FOLDER_ID", "DRIVE_FOLDER_ID"):
        v = os.environ.get(name)
        if v:
            return folder_id_from_text(v), name
    return None, None


def build_service(raw_json=None):
    """(service, 에러문구). 비밀번호·키는 환경변수에서만 읽는다."""
    raw = raw_json or os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None, "GOOGLE_SERVICE_ACCOUNT_JSON 환경변수가 없어요"
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        info = json.loads(raw)
        creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE_FULL])
        svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        svc._sa_email = info.get("client_email", "")
        return svc, None
    except Exception as e:
        return None, f"드라이브에 연결하지 못했어요 ({type(e).__name__}: {str(e)[:120]})"


def _google_reason(e):
    """HttpError 에서 구글이 준 이유 코드와 메시지를 꺼낸다. 없으면 (None, None)."""
    try:
        data = json.loads(e.content.decode("utf-8"))["error"]
        errs = data.get("errors") or [{}]
        return errs[0].get("reason"), data.get("message")
    except Exception:
        return None, None


def explain_error(e):
    """구글 API 에러를 대표님이 이해할 문장으로. 구글이 준 이유 코드도 뒤에 붙여서 원인을 숨기지 않는다."""
    msg = str(e)
    low = msg.lower()
    reason, gmsg = _google_reason(e)
    tail = f" (구글 응답: {reason or '-'}{' — ' + gmsg[:90] if gmsg else ''})" if (reason or gmsg) else ""
    if "storagequotaexceeded" in low or "do not have storage quota" in low:
        return ("서비스계정은 '내 드라이브' 폴더에 파일을 만들 저장 공간이 없어서 올릴 수 없어요. "
                "해결: ① 세무사 공유 폴더를 '공유 드라이브'로 옮기고 서비스계정을 멤버(콘텐츠 관리자)로 추가하거나, ② 대표 구글계정(OAuth)으로 올리는 방식으로 바꿔야 해요." + tail)
    if "accessnotconfigured" in low or "has not been used in project" in low or "api has not been used" in low or "is disabled" in low:
        return "Google Cloud 프로젝트에서 'Google Drive API'가 꺼져 있어요. 구글 클라우드 콘솔 → API 및 서비스 → 라이브러리 → 'Google Drive API' → 사용 설정을 눌러주세요." + tail
    if "ratelimit" in low or "userratelimitexceeded" in low:
        return "구글이 요청이 많다고 잠시 막았어요. 1~2분 뒤에 다시 눌러주세요." + tail
    if "insufficientfilepermissions" in low or "insufficient permission" in low or "does not have sufficient" in low or "forbidden" in low or " 403" in low:
        return "이 폴더에 올릴 권한이 없어요. 서비스계정을 폴더의 '편집자'로 공유했는지 확인해주세요(열람자는 목록만 볼 수 있어요)." + tail
    if "notfound" in low or " 404" in low or "file not found" in low:
        return "폴더를 찾지 못했어요. 폴더 ID가 맞는지, 서비스계정에 폴더가 공유됐는지 확인해주세요." + tail
    return f"{type(e).__name__}: {msg[:200]}"


def _q(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def list_children(svc, folder_id):
    """폴더 바로 아래 항목 [{id,name,mimeType,webViewLink,modifiedTime,size}]"""
    out, token = [], None
    while True:
        res = svc.files().list(
            q=f"'{folder_id}' in parents and trashed = false", fields="nextPageToken, files(id,name,mimeType,webViewLink,modifiedTime,size)",
            pageSize=200, pageToken=token, supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        out += res.get("files", [])
        token = res.get("nextPageToken")
        if not token:
            return out


def get_folder(svc, folder_id):
    return svc.files().get(fileId=folder_id, fields="id,name,mimeType,driveId,webViewLink", supportsAllDrives=True).execute()


def find_subfolder(svc, parent_id, name):
    for c in list_children(svc, parent_id):
        if c["mimeType"] == FOLDER_MIME and c["name"] == name:
            return c["id"]
    return None


def ensure_subfolder(svc, parent_id, name):
    """이름이 같은 하위 폴더를 찾고, 없으면 만든다. 반환 (폴더ID, 새로 만들었는지)"""
    fid = find_subfolder(svc, parent_id, name)
    if fid:
        return fid, False
    meta = svc.files().create(body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}, fields="id", supportsAllDrives=True).execute()
    return meta["id"], True


def _run_upload(req):
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp


def upload_bytes(svc, folder_id, name, data, mime="application/octet-stream"):
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
    req = svc.files().create(body={"name": name, "parents": [folder_id]}, media_body=media, fields="id,name,webViewLink", supportsAllDrives=True)
    return _run_upload(req)


def replace_or_upload(svc, folder_id, name, data, mime="application/octet-stream"):
    """우리가 만든 파일(자동 생성 엑셀·메모)용: 같은 이름이 있으면 내용을 교체하고, 없으면 새로 올린다. 반환 (파일정보, 교체했는지)"""
    from googleapiclient.http import MediaIoBaseUpload
    for c in list_children(svc, folder_id):
        if c["name"] == name and c["mimeType"] != FOLDER_MIME:
            media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=True, chunksize=8 * 1024 * 1024)
            req = svc.files().update(fileId=c["id"], media_body=media, fields="id,name,webViewLink", supportsAllDrives=True)
            return _run_upload(req), True
    return upload_bytes(svc, folder_id, name, data, mime), False


def delete_file(svc, file_id):
    svc.files().delete(fileId=file_id, supportsAllDrives=True).execute()


def unique_name(existing_names, name):
    """같은 이름이 이미 있으면 덮어쓰지 않고 (2), (3) 을 붙인다."""
    if name not in existing_names:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    i = 2
    while True:
        cand = f"{stem} ({i})" + (f".{ext}" if ext else "")
        if cand not in existing_names:
            return cand
        i += 1


def diagnose(svc, folder_id):
    """연결 점검: ① 폴더 읽기 ② 작은 파일 올리기→지우기. 반환 [(단계, 성공여부, 설명)]"""
    steps = []
    try:
        f = get_folder(svc, folder_id)
        kind = "공유 드라이브" if f.get("driveId") else "내 드라이브(개인)"
        steps.append(("폴더 읽기", True, f"'{f['name']}' ({kind})"))
    except Exception as e:
        steps.append(("폴더 읽기", False, explain_error(e)))
        return steps
    try:
        r = upload_bytes(svc, folder_id, ".연결점검(자동삭제).txt", "check".encode("utf-8"), "text/plain")
        try:
            delete_file(svc, r["id"])
            steps.append(("파일 올리기·지우기", True, "올리기와 삭제가 모두 돼요"))
        except Exception as e:
            steps.append(("파일 올리기·지우기", True, f"올리기는 됐는데 점검 파일을 못 지웠어요 — 폴더의 '.연결점검(자동삭제).txt'를 직접 지워주세요 ({explain_error(e)})"))
    except Exception as e:
        steps.append(("파일 올리기·지우기", False, explain_error(e)))
    return steps
