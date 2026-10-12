import os
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pandas as pd
import streamlit as st
from supabase import create_client

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 레포 루트의 team_input.py
from team_input import (FLOW_LABEL, STAGE_LABEL, build_instruction_items, days_since, payment_verdict,
                        placement_issues, placement_stats, summarize_flows)

st.set_page_config(page_title="Team Input", layout="wide")

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

TODAY = datetime.now(timezone(timedelta(hours=9))).date()
STAFF = ["곽재선", "이단우", "김선재", "구정회", "가상인턴"]
STATE_ICON = {"ok": "🟢", "warn": "🟡", "bad": "🔴", "unknown": "⚪"}


def tryq(fn, default):
    try:
        return fn(), None
    except Exception as e:
        return default, f"{type(e).__name__}: {str(e)[:140]}"


def fetch_all(table, cols="*", filt=None, order=None, page=1000):
    """PostgREST 는 한 번에 1000줄까지만 준다 → 나눠서 전부 읽는다."""
    out, start = [], 0
    while True:
        q = SUPA.table(table).select(cols)
        if filt:
            q = filt(q)
        if order:
            q = q.order(order, desc=True)
        rows = q.range(start, start + page - 1).execute().data
        out += rows
        if len(rows) < page:
            return out
        start += page


@st.cache_data(ttl=30)
def load_person(person):
    pay, e_pay = tryq(lambda: fetch_all("payment_requests", "id,influencer_name,amount,currency,status,created_at,report_complete,content_link,id_doc_link",
                                        lambda q: q.eq("submitted_by", person), "created_at"), [])
    links, _ = tryq(lambda: fetch_all("payment_request_campaigns", "payment_request_id"), [])
    att, e_att = tryq(lambda: fetch_all("staff_upload_attempts", "*", lambda q: q.eq("person", person), "created_at"), [])
    plc, e_plc = tryq(lambda: fetch_all("influencer_placements", "*", lambda q: q.eq("assigned_to", person), "updated_at"), [])
    camps, e_camp = tryq(lambda: fetch_all("sales_campaigns", "*", lambda q: q.eq("created_by", person), "created_at"), []) if person in ("김선재", "가상인턴") else ([], None)
    acts, _ = tryq(lambda: SUPA.table("daily_activity_log").select("created_at").eq("staff_name", person).order("created_at", desc=True).limit(200).execute().data, [])
    return {"pay": pay, "e_pay": e_pay, "links": {l["payment_request_id"] for l in links}, "att": att, "e_att": e_att,
            "plc": plc, "e_plc": e_plc, "camps": camps, "e_camp": e_camp, "acts": acts}


st.title("👥 Team Input — 직원 입력 현황")
st.caption("직원이 입력한 데이터가 화면에 안 보일 때, **시도도 안 한 건지 · 하다 멈춘 건지 · 미완성인 건지**를 구분해서 보고, 개선 지시를 보냅니다. "
           "여기서는 보기만 하고, 맨 아래에서 '할 일로 보내기'를 눌렀을 때만 직원 화면에 전달돼요.")
if st.session_state.get("ti_flash"):
    st.success(st.session_state.pop("ti_flash"))

top1, top2 = st.columns([1, 3])
person = top1.selectbox("직원", STAFF, key="ti_person")
if top2.button("🔄 새로고침", key="ti_refresh"):
    load_person.clear(); st.rerun()

d = load_person(person)
att_ok = d["e_att"] is None
verdict = payment_verdict(len(d["pay"]), d["att"], att_ok)
flows = summarize_flows(d["att"]) if att_ok else []
stats = placement_stats(d["plc"], TODAY)

# ── 한눈에 ──
st.subheader(f"{STATE_ICON[verdict[0]]} {person}")
m1, m2, m3, m4 = st.columns(4)
m1.metric("등록된 송금요청", f"{len(d['pay'])}건")
m2.metric("송금 등록 시도", f"{len(flows)}번" if att_ok else "기록 없음(테이블 미적용)")
last_act = d["acts"][0]["created_at"] if d["acts"] else None
m3.metric("업무보고 마지막 작성", f"{days_since(last_act)}일 전" if last_act else "기록 없음")
m4.metric("매칭 현황 건수", f"{stats['total']}건")
if verdict[0] == "unknown":
    st.info(f"⚪ {verdict[1]}")
elif verdict[0] == "bad":
    st.error(verdict[1])
elif verdict[0] == "warn":
    st.warning(verdict[1])
else:
    st.success(verdict[1])

# ── 1) 송금정보 등록 흔적 ──
st.markdown("### 🧾 송금정보 등록 — 올린 흔적")
if not att_ok:
    st.warning(
        "시도 기록 테이블(`staff_upload_attempts`)이 아직 DB에 없어요. `brandslamteamspace` 레포의 "
        "`migrations/20261012_staff_upload_attempts.sql`을 적용하면 그때부터 '불러오기·확인 중·막힘·저장·오류'가 한 줄씩 쌓여요. "
        f"(원인: {d['e_att']})")
else:
    first_any = d["att"][-1]["created_at"][:10] if d["att"] else None
    st.caption("이 기록은 기록 기능이 켜진 뒤부터 쌓여요. 금액·이름·계좌·링크는 저장하지 않고 건수와 빠진 항목 이름만 남겨요."
               + (f" ({person} 첫 기록: {first_any})" if first_any else ""))
    if flows:
        st.dataframe(pd.DataFrame([{
            "마지막 시각": (f["last"] or "")[:16].replace("T", " "),
            "결과": FLOW_LABEL.get(f["state"], f["state"]),
            "파일": f.get("file") or "-",
            "전체": f.get("n_rows") if f.get("n_rows") is not None else "-",
            "등록 가능/저장": f.get("n_ok") if f.get("n_ok") is not None else "-",
            "빠진 항목": ", ".join(f"{k} {v}건" for k, v in (f.get("missing") or {}).items()) or "-",
            "메모": f.get("detail") or "",
        } for f in flows]), hide_index=True, use_container_width=True)
        with st.expander("단계별 원본 기록 보기"):
            st.dataframe(pd.DataFrame([{
                "시각": (a.get("created_at") or "")[:19].replace("T", " "), "단계": STAGE_LABEL.get(a.get("stage"), a.get("stage")),
                "파일": a.get("file_name") or "", "전체": a.get("n_rows"), "가능": a.get("n_ok"), "막힘": a.get("n_blocked"),
                "빠진 항목": ", ".join(f"{k} {v}건" for k, v in (a.get("missing") or {}).items()), "메모": a.get("detail") or "",
            } for a in sorted(d["att"], key=lambda r: str(r.get("created_at")), reverse=True)]), hide_index=True, use_container_width=True)
    else:
        st.info("시도 기록이 없어요.")
if d["pay"]:
    with st.expander(f"저장된 송금요청 {len(d['pay'])}건 (캠페인 연결 여부 포함)"):
        st.dataframe(pd.DataFrame([{
            "등록일": (p.get("created_at") or "")[:10], "이름": p.get("influencer_name"), "금액": p.get("amount"), "통화": p.get("currency"),
            "상태": p.get("status"), "캠페인 연결": "✅" if p["id"] in d["links"] else "⚠️ 없음",
            "콘텐츠링크": "✅" if p.get("content_link") else "⚠️", "신분증": "✅" if p.get("id_doc_link") else "⚠️",
        } for p in d["pay"]]), hide_index=True, use_container_width=True)
elif d["e_pay"]:
    st.warning(f"송금요청을 읽지 못했어요 ({d['e_pay']})")

# ── 2) 인플루언서 매칭 현황 ──
if d["plc"] or d["e_plc"]:
    st.markdown("### 📋 인플루언서 매칭 현황 — 입력 완성도")
    if d["e_plc"]:
        st.warning(f"읽지 못했어요 ({d['e_plc']})")
    else:
        st.caption("상태별: " + " · ".join(f"{k} {v}건" for k, v in stats["by_status"].items()))
        cols = st.columns(max(len(stats["counts"]), 1))
        for col, (k, v) in zip(cols, sorted(stats["counts"].items(), key=lambda kv: -kv[1])):
            col.metric(k, f"{v}건")
        only_bad = st.checkbox("업로드완료인데 링크/단가가 빠진 건, 예정일 지난 건만 보기 (중요한 것만)", value=True, key="ti_only_bad")
        rows = []
        for r in d["plc"]:
            iss = placement_issues(r, TODAY)
            key_iss = [i for i in iss if not i.startswith("가이드라인") and i != "예정일 없음"]
            if only_bad and not key_iss:
                continue
            rows.append({
                "브랜드": r.get("brand_name"), "인플루언서": r.get("influencer_name"), "상태": r.get("status"),
                "예정일": r.get("scheduled_date"), "단가": r.get("unit_price") if r.get("unit_price") is not None else "-", "링크": "✅" if r.get("content_link") else "-",
                "문제": " · ".join(iss), "최종수정": (r.get("updated_at") or "")[:10],
            })
        st.caption(f"{len(rows)}건 표시")
        if rows:
            df = pd.DataFrame(rows)
            st.dataframe(df, hide_index=True, use_container_width=True, height=360)
            st.download_button("⬇️ CSV로 받기 (엑셀 호환)", df.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"{person}_입력현황.csv", mime="text/csv", key="ti_csv")

# ── 3) 김선재: 캠페인 등록 완성도 ──
if d["camps"] or d["e_camp"]:
    st.markdown("### 📝 캠페인 등록 — 입력 완성도")
    if d["e_camp"]:
        st.warning(f"읽지 못했어요 ({d['e_camp']})")
    else:
        rows = []
        for c in d["camps"]:
            miss = [lab for lab, v in (("계약서", c.get("contract_url")), ("인보이스 파일", c.get("invoice_url")),
                                       ("인보이스 금액", c.get("invoice_amount"))) if not v]
            if "invoice_vat_type" in c and not c.get("invoice_vat_type"):
                miss.append("부가세 구분")
            if "invoice_issue_date" in c and not c.get("invoice_issue_date"):
                miss.append("발행일")
            rows.append({"캠페인": c.get("campaign_name"), "오픈일": c.get("open_date"), "상태": c.get("status"),
                         "빠진 항목": ", ".join(miss) or "✅ 완성"})
        incomplete = sum(1 for r in rows if r["빠진 항목"] != "✅ 완성")
        st.caption(f"전체 {len(rows)}건 중 빠진 항목이 있는 캠페인 {incomplete}건 (마진 계산은 인보이스 금액·부가세 구분이 있어야 정확해요)")
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

# ── 4) 개선 지시 ──
st.markdown("### ✍️ 개선 지시")
items = build_instruction_items(person, verdict, stats, flows, TODAY)
if not items:
    st.success("지금 데이터로는 지시할 항목이 없어요.")
else:
    st.caption("아래는 현황으로 만든 초안이에요. 고쳐 써도 되고, 필요 없는 건 체크를 빼세요. **아직 아무것도 전달되지 않았어요** — 맨 아래 버튼을 눌러야 보내져요.")
    with st.form("ti_send_form"):
        texts = []
        for i, t in enumerate(items):
            on = st.checkbox("보내기", value=True, key=f"ti_on_{person}_{i}")
            tx = st.text_area("문구", value=t, key=f"ti_tx_{person}_{i}", height=80, label_visibility="collapsed")
            texts.append((on, tx.strip()))
        send = st.form_submit_button(f"📨 {person}님 '내 할 일'로 보내기 (여기서 전달돼요)", type="primary")
    if send:
        chosen = [tx for on, tx in texts if on and tx]
        if not chosen:
            st.error("보낼 문구가 없어요. 체크와 문구를 확인해주세요.")
        else:
            try:
                for tx in chosen:
                    SUPA.table("assigned_tasks").insert({"person": person, "category": "개선지시", "title": f"[개선 요청] {tx}"}).execute()
                st.session_state["ti_flash"] = f"✅ {person}님께 {len(chosen)}건을 할 일로 보냈어요. ({person}님 '내 할 일'에서 보여요)"
                st.rerun()
            except Exception as e:
                st.error(f"❌ 전달하지 못했어요 ({type(e).__name__}: {str(e)[:160]}) — 아무것도 보내지지 않았을 수 있어요. 다시 시도하거나 문구를 복사해 직접 전달해주세요.")
    with st.expander("📋 복사용 전체 문구"):
        st.code("\n".join(f"{n}. {t}" for n, t in enumerate(items, 1)), language=None)
