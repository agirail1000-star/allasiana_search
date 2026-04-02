"""
아시아나 마일리지 좌석 조회 프로그램
--------------------------------------
웹 UI : python app.py → http://localhost:5000
CLI   : python asiana_mileage_checker.py
진단  : python asiana_mileage_checker.py --diagnose
"""

import csv
import os
import shutil
import socket
import subprocess
import sys
import time
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# ── 사용자 설정 ────────────────────────────────────────────────────────────────
ORIGIN        = "ICN"
DESTINATION   = "NRT"
START_DATE    = "2026-07-01"
END_DATE      = "2026-07-31"
OUTPUT_CSV    = "asiana_mileage_seats.csv"
BETWEEN_MONTHS = 3     # 월 간 대기 시간(초)
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL       = "https://flyasiana.com"
MILEAGE_URL    = "https://flyasiana.com/I/KR/KO/MileageSeatSearch.do"
SCREENSHOT_DIR = Path("screenshots")

# 공항코드 → (지역코드, 도시코드) 매핑
# 아시아나 hidden input: departureArea1, departureCity1
AREA_CITY = {
    "ICN": ("KR", "SEL"), "GMP": ("KR", "SEL"),
    "NRT": ("JP", "TYO"), "HND": ("JP", "TYO"),
    "KIX": ("JP", "OSA"), "NGO": ("JP", "NGO"),
    "CTS": ("JP", "SPK"), "FUK": ("JP", "FUK"),
    "OKA": ("JP", "OKA"),
    "JFK": ("US", "NYC"), "LAX": ("US", "LAX"),
    "SFO": ("US", "SFO"), "ORD": ("US", "CHI"),
    "SEA": ("US", "SEA"), "HNL": ("US", "HNL"),
    "FRA": ("DE", "FRA"), "LHR": ("GB", "LON"),
    "CDG": ("FR", "PAR"), "AMS": ("NL", "AMS"),
    "VIE": ("AT", "VIE"), "ZRH": ("CH", "ZRH"),
    "HKG": ("HK", "HKG"), "SIN": ("SG", "SIN"),
    "BKK": ("TH", "BKK"), "MNL": ("PH", "MNL"),
    "PVG": ("CN", "SHA"), "PEK": ("CN", "BJS"),
    "CAN": ("CN", "CAN"), "SYD": ("AU", "SYD"),
}


# ── Chrome 경로 탐색 ──────────────────────────────────────────────────────────
def _find_chrome() -> str:
    """Windows에서 Chrome 실행 파일 경로를 찾습니다."""
    # 1) 흔한 고정 경로 + 환경변수 경로
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p

    # 2) Windows 레지스트리에서 찾기
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe")
            val, _ = winreg.QueryValueEx(key, "")
            winreg.CloseKey(key)
            if val and os.path.isfile(val):
                return val
        except OSError:
            pass

    # 3) PATH 에서 찾기
    found = shutil.which("chrome") or shutil.which("chrome.exe") or shutil.which("google-chrome")
    if found:
        return found

    raise FileNotFoundError(
        "Chrome 실행 파일을 찾을 수 없습니다. "
        "Chrome이 설치되어 있는지 확인해 주세요."
    )


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


# Chrome 프로세스 참조 (세션 종료 시 kill 필요)
_chrome_process: Optional[subprocess.Popen] = None

CHROME_DEBUG_PORT = 9222
CHROME_PROFILE_DIR = str(Path(__file__).parent / "chrome_profile")


# ── 드라이버 초기화 ────────────────────────────────────────────────────────────
def init_driver() -> webdriver.Chrome:
    """
    Chrome을 일반 브라우저로 실행한 뒤 remote-debugging-port로 Selenium을 연결합니다.
    이 방식은 navigator.webdriver=true가 되지 않아 사이트의 자동화 감지를 회피합니다.
    """
    global _chrome_process

    print("[init_driver] 시작...")

    # 이미 디버그 포트가 열려 있으면 기존 프로세스 재사용
    if not _is_port_in_use(CHROME_DEBUG_PORT):
        chrome_path = _find_chrome()
        print(f"[init_driver] Chrome 경로: {chrome_path}")

        # 전용 프로필 디렉토리 (기존 Chrome과 충돌 방지)
        os.makedirs(CHROME_PROFILE_DIR, exist_ok=True)

        cmd = [
            chrome_path,
            f"--remote-debugging-port={CHROME_DEBUG_PORT}",
            f"--user-data-dir={CHROME_PROFILE_DIR}",
            "--window-size=1280,900",
            "--lang=ko-KR",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        print(f"[init_driver] Chrome 실행: {' '.join(cmd)}")
        _chrome_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Chrome이 디버그 포트를 열 때까지 대기
        for i in range(30):
            if _is_port_in_use(CHROME_DEBUG_PORT):
                print(f"[init_driver] 디버그 포트 {CHROME_DEBUG_PORT} 열림 ({i*0.5:.1f}초)")
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(
                f"Chrome 디버그 포트({CHROME_DEBUG_PORT})가 열리지 않았습니다. "
                "이미 실행 중인 Chrome을 모두 종료한 뒤 다시 시도해 주세요."
            )
    else:
        print(f"[init_driver] 포트 {CHROME_DEBUG_PORT} 이미 열려 있음 — 기존 Chrome 재사용")

    # chromedriver 설치/확인
    print("[init_driver] chromedriver 준비 중...")
    driver_path = ChromeDriverManager().install()
    print(f"[init_driver] chromedriver 경로: {driver_path}")
    service = Service(driver_path)

    # Selenium을 디버그 포트에 연결
    options = Options()
    options.add_experimental_option("debuggerAddress", f"127.0.0.1:{CHROME_DEBUG_PORT}")

    print("[init_driver] Selenium 연결 중...")
    driver = webdriver.Chrome(service=service, options=options)
    print("[init_driver] 연결 완료!")
    return driver


def kill_chrome():
    """Chrome 프로세스를 종료합니다."""
    global _chrome_process
    if _chrome_process:
        try:
            _chrome_process.terminate()
            _chrome_process.wait(timeout=5)
        except Exception:
            try:
                _chrome_process.kill()
            except Exception:
                pass
        _chrome_process = None


# ── 유틸 ──────────────────────────────────────────────────────────────────────
def save_screenshot(driver: webdriver.Chrome, name: str) -> str:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str(SCREENSHOT_DIR / f"{name}_{ts}.png")
    try:
        driver.save_screenshot(path)
    except Exception:
        pass
    return path


def save_html(driver: webdriver.Chrome, name: str) -> str:
    SCREENSHOT_DIR.mkdir(exist_ok=True)
    ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = str(SCREENSHOT_DIR / f"{name}_{ts}.html")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
    except Exception:
        pass
    return path


def is_error_page(driver: webdriver.Chrome) -> bool:
    try:
        body = driver.find_element(By.TAG_NAME, "body").text
        return any(k in body for k in ["일시적인 오류", "temporary error", "서비스 이용에 불편"])
    except Exception:
        return False


# ── 폼 채우기 + 검색 실행 ──────────────────────────────────────────────────────
def fill_form_and_search(driver: webdriver.Chrome,
                          origin: str, dest: str,
                          year: int, month: int) -> bool:
    """
    Hidden input 값을 직접 설정하고 retrieveMileageSeatSearch()를 호출합니다.
    달력 결과가 로드되면 True 반환.
    """
    dep_area, dep_city = AREA_CITY.get(origin, ("", ""))
    arr_area, arr_city = AREA_CITY.get(dest,   ("", ""))
    date_str = f"{year}{month:02d}01"

    driver.get(MILEAGE_URL)
    time.sleep(2)

    try:
        driver.execute_script(f"""
            // 출발지
            document.getElementById('departureArea1').value    = '{dep_area}';
            document.getElementById('departureAirport1').value = '{origin}';
            document.getElementById('departureCity1').value    = '{dep_city}';
            // 도착지
            document.getElementById('arrivalArea1').value    = '{arr_area}';
            document.getElementById('arrivalAirport1').value = '{dest}';
            document.getElementById('arrivalCity1').value    = '{arr_city}';
            // 날짜 (편도이므로 출발일만 사용)
            document.getElementById('departureDate1').value = '{date_str}';
            document.getElementById('arrivalDate1').value   = '{date_str}';
            // 편도
            document.getElementById('tripType').value = 'OW';
            // 검색 실행
            retrieveMileageSeatSearch(false);
        """)
    except Exception as e:
        return False

    # 달력 로드 대기
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                "#depCalendar, .mileage_calendar_wrap, "
                ".flight_calendar_wrap, [id*='Calendar']"))
        )
        time.sleep(1)
        return True
    except Exception:
        save_screenshot(driver, "calendar_load_fail")
        return False


# ── 달력에서 전체 데이터 추출 ─────────────────────────────────────────────────
def extract_calendar_data(driver: webdriver.Chrome) -> Dict[str, List[Dict]]:
    """
    달력의 data-economy / data-business / data-businessP 속성에서
    날짜별 항공편 정보를 한 번에 추출합니다.

    반환값:
      {
        "2026-07-01": [
          {"flight_no": "OZ0102", "dep_time": "08:25",
           "economy": "O", "business": "X", "upgrade": "O"},
          ...
        ],
        ...
      }
    """
    result = driver.execute_script(r"""
        var out = {};

        // ── 출발 달력(depCalendar)만 선택 ───────────────────────────────
        var depCal = document.getElementById('depCalendar');
        if (!depCal) depCal = document;
        var links = depCal.querySelectorAll('a[data-date]');

        links.forEach(function(link) {
            var rawDate = link.getAttribute('data-date') || '';
            if (!rawDate) return;
            var date = rawDate.replace(/\./g, '-');

            function parseAttr(attr) {
                try { return JSON.parse(link.getAttribute(attr) || '[]'); }
                catch(e) { return []; }
            }

            var ecoList = parseAttr('data-economy');
            var bizAll  = parseAttr('data-business');

            // ── data-business 를 bkgCd 기준으로 분리 ─────────────────────
            //   bkgCd=I → 비즈니스 클래스 좌석
            //   bkgCd=P → 비즈니스 좌석승급
            var bizList = bizAll.filter(function(f){ return f.bkgCd !== 'P'; });
            var upgList = bizAll.filter(function(f){ return f.bkgCd === 'P'; });

            // ── 항공편 키 수집 ───────────────────────────────────────────
            var flights = {};
            function initFlight(f) {
                if (!flights[f.fltNbr]) {
                    var dt   = f.deptrDt || '';
                    var hhmm = dt.length >= 12
                        ? dt.substring(8,10) + ':' + dt.substring(10,12) : '';
                    flights[f.fltNbr] = {
                        flight_no: 'OZ' + f.fltNbr,
                        dep_time:  hhmm,
                        economy: 'X', business: 'X', upgrade: 'X'
                    };
                }
            }
            ecoList.forEach(initFlight);
            bizList.forEach(initFlight);
            upgList.forEach(initFlight);

            // ── 좌석 판정 ────────────────────────────────────────────────
            //   soldOut=true  → 좌석 있음 (O)
            //   soldOut=false → 좌석 없음 (X)
            //   같은 항공편에 여러 행: 하나라도 soldOut=true면 O
            function applyCabin(list, field) {
                var map = {};
                list.forEach(function(f) {
                    // soldOut=true 가 가용(available)
                    var avail = f.soldOut === true;
                    map[f.fltNbr] = map[f.fltNbr] || avail;
                });
                Object.keys(map).forEach(function(key) {
                    if (flights[key]) {
                        flights[key][field] = map[key] ? 'O' : 'X';
                    }
                });
            }

            applyCabin(ecoList, 'economy');
            applyCabin(bizList, 'business');
            applyCabin(upgList, 'upgrade');

            var arr = Object.values(flights);
            if (arr.length > 0) {
                if (!out[date]) out[date] = [];
                arr.forEach(function(f) {
                    var dup = out[date].some(function(e){ return e.flight_no === f.flight_no; });
                    if (!dup) out[date].push(f);
                });
            }
        });

        return out;
    """)

    return result if result else {}


# ── 날짜 범위 생성 ─────────────────────────────────────────────────────────────
def date_range(start: str, end: str):
    current = datetime.strptime(start, "%Y-%m-%d")
    last    = datetime.strptime(end,   "%Y-%m-%d")
    while current <= last:
        yield current.strftime("%Y-%m-%d")
        current += timedelta(days=1)


def _months_in_range(start: str, end: str) -> List[tuple]:
    """날짜 범위에 포함된 (year, month) 목록을 반환합니다."""
    s = datetime.strptime(start, "%Y-%m-%d").replace(day=1)
    e = datetime.strptime(end,   "%Y-%m-%d")
    months = []
    while s <= e:
        months.append((s.year, s.month))
        if s.month == 12:
            s = s.replace(year=s.year+1, month=1)
        else:
            s = s.replace(month=s.month+1)
    return months


# ── 전체 조회 ─────────────────────────────────────────────────────────────────
def scrape(origin: str, dest: str, start_date: str, end_date: str,
           driver: webdriver.Chrome,
           stop_event=None,
           progress_cb=None,
           row_cb=None,
           log_cb=None) -> List[Dict]:

    def log(msg, cls="info"):
        if log_cb:
            log_cb(msg, cls)
        else:
            print(msg)

    target_dates = set(date_range(start_date, end_date))
    months       = _months_in_range(start_date, end_date)
    total        = len(target_dates)
    done         = 0
    rows: List[Dict] = []

    for idx, (year, month) in enumerate(months):
        if stop_event and stop_event.is_set():
            break

        log(f"{year}년 {month}월 조회 중...", "info")

        ok = fill_form_and_search(driver, origin, dest, year, month)
        if not ok:
            log(f"{year}/{month:02d} 달력 로드 실패 — 스크린샷 확인", "err")
            save_html(driver, f"fail_{year}{month:02d}")
        else:
            log(f"{year}/{month:02d} 달력 로드 완료 — 데이터 추출 중...", "ok")
            if idx == 0:
                save_html(driver, "calendar_debug")  # 첫 번째 달 HTML 저장

        cal_data = extract_calendar_data(driver)
        log(f"  달력에서 {len(cal_data)}일치 데이터 추출", "info")

        # 조회 대상 날짜만 필터링
        for date_str in sorted(target_dates):
            if stop_event and stop_event.is_set():
                break

            d = datetime.strptime(date_str, "%Y-%m-%d")
            if d.year != year or d.month != month:
                continue

            done += 1
            if progress_cb:
                progress_cb(done, total, date_str)

            flights = cal_data.get(date_str, [])

            if not flights:
                row = {
                    "date": date_str, "origin": origin, "dest": dest,
                    "flight_no": "-", "dep_time": "-",
                    "economy": "-", "business": "-", "upgrade": "-",
                }
                rows.append(row)
                if row_cb:
                    row_cb(row)
            else:
                for f in sorted(flights, key=lambda x: x.get("dep_time", "")):
                    row = {
                        "date":      date_str,
                        "origin":    origin,
                        "dest":      dest,
                        "flight_no": f.get("flight_no", ""),
                        "dep_time":  f.get("dep_time",  ""),
                        "economy":   f.get("economy",   "-"),
                        "business":  f.get("business",  "-"),
                        "upgrade":   f.get("upgrade",   "-"),
                    }
                    rows.append(row)
                    if row_cb:
                        row_cb(row)

        if idx < len(months) - 1 and not (stop_event and stop_event.is_set()):
            time.sleep(BETWEEN_MONTHS)

    return rows


# ── 진단 모드 ─────────────────────────────────────────────────────────────────
def run_diagnose():
    print("=" * 60)
    print("  [진단 모드]")
    print("=" * 60)
    driver = init_driver()
    driver.get(BASE_URL)
    print("\n1. 아시아나 로그인")
    print("2. 마일리지 검색 결과 페이지까지 이동")
    input("3. 결과 보이면 Enter...")
    url       = driver.current_url
    html_path = save_html(driver, "diagnose")
    img_path  = save_screenshot(driver, "diagnose")
    print(f"\nURL  : {url}")
    print(f"HTML : {html_path}")
    print(f"IMG  : {img_path}")
    driver.quit()
    kill_chrome()


# ── CLI 메인 ──────────────────────────────────────────────────────────────────
def main():
    if "--diagnose" in sys.argv:
        run_diagnose()
        return

    print("=" * 60)
    print(f"  아시아나 마일리지 좌석 조회  {ORIGIN} → {DESTINATION}")
    print(f"  {START_DATE} ~ {END_DATE}")
    print("=" * 60)

    driver = init_driver()
    driver.get(BASE_URL)
    print("\n로그인 후 Enter...")
    input()

    rows = scrape(ORIGIN, DESTINATION, START_DATE, END_DATE, driver)

    fieldnames = ["date", "origin", "dest", "flight_no", "dep_time",
                  "economy", "business", "upgrade"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n완료! {len(rows)}행 저장: {OUTPUT_CSV}")
    driver.quit()
    kill_chrome()


if __name__ == "__main__":
    main()
