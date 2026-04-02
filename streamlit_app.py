"""
아시아나 마일리지 좌석 조회 - Streamlit UI
실행: streamlit run streamlit_app.py
"""

import csv
import io
import threading
import time
from datetime import date

import streamlit as st
import pandas as pd

from asiana_mileage_checker import (
    BASE_URL,
    init_driver,
    kill_chrome,
    scrape,
)

# ── 공항 데이터 ────────────────────────────────────────────────────────────────
AIRPORTS = [
    ("ICN", "인천국제공항", "서울/인천", "Seoul Incheon"),
    ("GMP", "김포국제공항", "서울/김포", "Seoul Gimpo"),
    ("NRT", "나리타국제공항", "도쿄/나리타", "Tokyo Narita"),
    ("HND", "하네다공항", "도쿄/하네다", "Tokyo Haneda"),
    ("KIX", "간사이국제공항", "오사카", "Osaka Kansai"),
    ("NGO", "중부국제공항", "나고야", "Nagoya Centrair"),
    ("FUK", "후쿠오카공항", "후쿠오카", "Fukuoka"),
    ("CTS", "신치토세공항", "삿포로", "Sapporo Chitose"),
    ("OKA", "나하공항", "오키나와", "Okinawa Naha"),
    ("JFK", "존F케네디국제공항", "뉴욕", "New York JFK"),
    ("LAX", "로스앤젤레스국제공항", "LA", "Los Angeles"),
    ("SFO", "샌프란시스코국제공항", "샌프란시스코", "San Francisco"),
    ("ORD", "오헤어국제공항", "시카고", "Chicago O'Hare"),
    ("SEA", "시애틀타코마국제공항", "시애틀", "Seattle Tacoma"),
    ("HNL", "호놀룰루국제공항", "호놀룰루", "Honolulu"),
    ("FRA", "프랑크푸르트국제공항", "프랑크푸르트", "Frankfurt"),
    ("LHR", "히스로공항", "런던", "London Heathrow"),
    ("CDG", "샤를드골국제공항", "파리", "Paris CDG"),
    ("AMS", "스키폴국제공항", "암스테르담", "Amsterdam Schiphol"),
    ("VIE", "빈국제공항", "빈", "Vienna"),
    ("ZRH", "취리히공항", "취리히", "Zurich"),
    ("HKG", "홍콩국제공항", "홍콩", "Hong Kong"),
    ("SIN", "창이국제공항", "싱가포르", "Singapore Changi"),
    ("BKK", "수완나품국제공항", "방콕", "Bangkok Suvarnabhumi"),
    ("MNL", "니노이아키노국제공항", "마닐라", "Manila"),
    ("PVG", "푸동국제공항", "상하이", "Shanghai Pudong"),
    ("PEK", "베이징수도국제공항", "베이징", "Beijing Capital"),
    ("CAN", "바이윈국제공항", "광저우", "Guangzhou Baiyun"),
    ("SYD", "킹스포드스미스국제공항", "시드니", "Sydney"),
    ("TPE", "타오위안국제공항", "타이페이", "Taipei Taoyuan"),
    ("DXB", "두바이국제공항", "두바이", "Dubai"),
    ("DEL", "인디라간디국제공항", "뉴델리", "New Delhi"),
    ("HAN", "노이바이국제공항", "하노이", "Hanoi"),
    ("SGN", "떤선녓국제공항", "호치민", "Ho Chi Minh"),
    ("KUL", "쿠알라룸푸르국제공항", "쿠알라룸푸르", "Kuala Lumpur"),
    ("CGK", "수카르노하타국제공항", "자카르타", "Jakarta"),
    ("MEL", "멜버른공항", "멜버른", "Melbourne"),
]

AIRPORT_OPTIONS = {f"{code} - {city} ({name})": code for code, name, city, en in AIRPORTS}


# ── 페이지 설정 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="아시아나 마일리지 좌석 조회",
    page_icon="✈",
    layout="wide",
)

# ── 세션 상태 초기화 ──────────────────────────────────────────────────────────
for key, default in {
    "driver": None,
    "state": "idle",          # idle | login_wait | ready | searching | done
    "logs": [],
    "results": [],
    "cancel_event": None,
    "search_thread": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default


def add_log(msg, level="info"):
    st.session_state.logs.append((time.strftime("%H:%M:%S"), level, msg))


# ── 헤더 ──────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="background:linear-gradient(135deg,#003087,#0057b8);color:#fff;
    border-radius:14px;padding:20px 28px;margin-bottom:16px;display:flex;
    align-items:center;justify-content:space-between;">
  <div style="display:flex;align-items:center;gap:14px;">
    <span style="font-size:1.9rem;">✈</span>
    <div>
      <h2 style="margin:0;font-size:1.3rem;">아시아나 마일리지 좌석 조회</h2>
      <p style="margin:0;font-size:.8rem;opacity:.8;">
        로컬 PC에서 실행 · Chrome 브라우저 자동화
      </p>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── 상태 표시 ─────────────────────────────────────────────────────────────────
state = st.session_state.state
state_map = {
    "idle":       ("🔴", "세션 없음"),
    "login_wait": ("🟡", "로그인 대기 중"),
    "ready":      ("🟢", "세션 활성 — 조회 가능"),
    "searching":  ("🟢", "조회 중..."),
    "done":       ("🟢", "조회 완료 — 다른 노선 조회 가능"),
}
dot, label = state_map.get(state, ("⚪", state))
st.markdown(f"**세션 상태:** {dot} {label}")

# ── 세션 관리 ─────────────────────────────────────────────────────────────────
col_open, col_login, col_quit = st.columns([1, 1, 1])

with col_open:
    if st.button("🌐 브라우저 열기", disabled=state != "idle", use_container_width=True):
        try:
            add_log("Chrome 브라우저 실행 중...")
            driver = init_driver()
            st.session_state.driver = driver
            add_log("flyasiana.com 으로 이동 중...")
            driver.get(BASE_URL)
            st.session_state.state = "login_wait"
            add_log("Chrome 창에서 아시아나에 로그인해 주세요.", "warn")
            st.rerun()
        except Exception as e:
            add_log(f"브라우저 오류: {e}", "err")
            kill_chrome()
            st.session_state.state = "idle"
            st.rerun()

with col_login:
    if st.button("✔ 로그인 완료", disabled=state != "login_wait", use_container_width=True):
        st.session_state.state = "ready"
        add_log("로그인 완료 — 세션 활성화", "ok")
        st.rerun()

with col_quit:
    if st.button("⏹ 세션 종료", disabled=state == "idle", use_container_width=True,
                  type="secondary"):
        # 검색 중이면 취소
        if st.session_state.cancel_event:
            st.session_state.cancel_event.set()
        driver = st.session_state.driver
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
        kill_chrome()
        st.session_state.driver = None
        st.session_state.state = "idle"
        st.session_state.results = []
        add_log("세션이 종료되었습니다.", "warn")
        st.rerun()

# 로그인 안내 배너
if state == "login_wait":
    st.warning("🔐 Chrome 창에서 아시아나 회원 로그인을 완료한 뒤 **[✔ 로그인 완료]** 버튼을 눌러주세요.")

# ── 조회 설정 ─────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("조회 설정")

col1, col2, col3, col4 = st.columns(4)

with col1:
    origin_label = st.selectbox(
        "출발지",
        options=list(AIRPORT_OPTIONS.keys()),
        index=0,
        disabled=state not in ("ready", "done"),
    )
with col2:
    dest_label = st.selectbox(
        "도착지",
        options=list(AIRPORT_OPTIONS.keys()),
        index=2,  # NRT
        disabled=state not in ("ready", "done"),
    )
with col3:
    start_date = st.date_input("시작 날짜", value=date(2026, 7, 1),
                               disabled=state not in ("ready", "done"))
with col4:
    end_date = st.date_input("종료 날짜", value=date(2026, 7, 31),
                             disabled=state not in ("ready", "done"))

origin_code = AIRPORT_OPTIONS[origin_label]
dest_code = AIRPORT_OPTIONS[dest_label]

# ── 조회 시작/취소 ────────────────────────────────────────────────────────────
col_start, col_cancel = st.columns([1, 1])

with col_start:
    if st.button("▶ 조회 시작", disabled=state not in ("ready", "done"),
                 use_container_width=True, type="primary"):
        st.session_state.results = []
        st.session_state.logs = []
        st.session_state.state = "searching"
        cancel_evt = threading.Event()
        st.session_state.cancel_event = cancel_evt

        add_log(f"조회 시작: {origin_code} → {dest_code}  {start_date} ~ {end_date}")

        # 동기 실행 (Streamlit은 스크립트 재실행 방식이라 스레드보다 직접 실행이 안정적)
        driver = st.session_state.driver
        if not driver:
            add_log("드라이버가 없습니다. 브라우저를 다시 열어주세요.", "err")
            st.session_state.state = "idle"
            st.rerun()
        else:
            collected_rows = []
            progress_bar = st.progress(0, text="준비 중...")
            log_area = st.empty()

            def on_progress(current, total, d):
                pct = current / total
                progress_bar.progress(pct, text=f"[{current}/{total}] {d} 조회 중...")

            def on_row(row):
                collected_rows.append(row)

            def on_log(msg, cls):
                add_log(msg, cls)

            try:
                rows = scrape(
                    origin_code, dest_code,
                    str(start_date), str(end_date),
                    driver,
                    stop_event=cancel_evt,
                    progress_cb=on_progress,
                    row_cb=on_row,
                    log_cb=on_log,
                )
                st.session_state.results = rows
                if cancel_evt.is_set():
                    add_log(f"조회 취소됨 — {len(rows)}건 저장", "warn")
                else:
                    add_log(f"조회 완료 — 총 {len(rows)}건", "ok")
                st.session_state.state = "done"
            except Exception as e:
                add_log(f"조회 오류: {e}", "err")
                st.session_state.state = "ready"
            finally:
                st.session_state.cancel_event = None
                st.rerun()

with col_cancel:
    if st.button("■ 조회 취소", disabled=state != "searching",
                 use_container_width=True):
        if st.session_state.cancel_event:
            st.session_state.cancel_event.set()
            add_log("취소 요청됨...", "warn")

# ── 로그 ──────────────────────────────────────────────────────────────────────
if st.session_state.logs:
    with st.expander("📋 로그", expanded=True):
        color_map = {"ok": "green", "err": "red", "warn": "orange", "info": "blue"}
        for ts, level, msg in st.session_state.logs[-30:]:
            c = color_map.get(level, "gray")
            st.markdown(f"<span style='color:{c};font-family:monospace;font-size:.85rem;'>"
                        f"[{ts}] {msg}</span>", unsafe_allow_html=True)

# ── 결과 테이블 ───────────────────────────────────────────────────────────────
if st.session_state.results:
    st.markdown("---")
    rows = st.session_state.results
    st.subheader(f"조회 결과 ({len(rows)}건)")

    df = pd.DataFrame(rows)
    df.columns = ["날짜", "출발", "도착", "항공편", "출발시간", "이코노미", "비즈니스", "비즈니스승급"]

    def style_seat(val):
        if val == "O":
            return "color: #276749; font-weight: 800;"
        elif val == "X":
            return "color: #a0aec0;"
        return "color: #e2e8f0;"

    styled = df.style.applymap(style_seat, subset=["이코노미", "비즈니스", "비즈니스승급"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=600)

    # CSV 다운로드
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=[
        "date", "origin", "dest", "flight_no", "dep_time",
        "economy", "business", "upgrade"
    ])
    writer.writeheader()
    writer.writerows(rows)
    csv_str = buf.getvalue()

    st.download_button(
        "⬇ CSV 다운로드",
        data=csv_str.encode("utf-8-sig"),
        file_name="asiana_mileage_seats.csv",
        mime="text/csv",
    )

# ── 안내 ──────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 사용 방법")
    st.markdown("""
1. **브라우저 열기** 클릭 → Chrome 실행
2. Chrome 창에서 **아시아나 로그인**
3. **로그인 완료** 클릭
4. 출발지/도착지/날짜 설정
5. **조회 시작** 클릭
6. 조회 완료 후 다른 노선도 바로 조회 가능
7. 끝나면 **세션 종료** 클릭
    """)
    st.markdown("---")
    st.markdown("""
    **⚠ 주의사항**
    - 이 앱은 **로컬 PC**에서만 실행 가능합니다
    - Chrome 브라우저가 설치되어 있어야 합니다
    - 아시아나 회원 로그인이 필요합니다
    - Streamlit Cloud에서는 작동하지 않습니다
    """)
