"""
아시아나 마일리지 좌석 조회 - 웹 서버
실행: python app.py  →  http://localhost:5000
"""

import csv
import io
import json
import queue
import threading

from flask import Flask, Response, jsonify, render_template, request, send_file

from asiana_mileage_checker import (
    BASE_URL,
    init_driver,
    kill_chrome,
    save_html,
    save_screenshot,
    scrape,
)

app = Flask(__name__)

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
# session_state: "idle" | "login_wait" | "ready" | "searching"
_session = {
    "state":        "idle",
    "driver":       None,
    "login_event":  threading.Event(),
}

_job = {
    "running":      False,
    "cancel_event": threading.Event(),
    "msg_queue":    queue.Queue(),
    "results":      [],
    "csv_data":     None,
}


def _send(msg: dict):
    _job["msg_queue"].put(msg)


# ── 세션 시작 (최초 1회 로그인) ───────────────────────────────────────────────
def _open_browser():
    """Chrome을 열고 로그인을 기다립니다. 이후 세션은 계속 유지됩니다."""
    try:
        _send({"type": "log", "text": "Chrome 브라우저 실행 중...", "cls": "info"})
        driver = init_driver()
        _session["driver"] = driver
        _session["state"]  = "login_wait"

        _send({"type": "log", "text": "flyasiana.com 으로 이동 중...", "cls": "info"})
        driver.get(BASE_URL)

        _send({"type": "login_required"})
        _session["login_event"].wait()

        _session["state"] = "ready"
        _send({"type": "session_ready"})
    except Exception as e:
        import traceback
        traceback.print_exc()
        _session["state"] = "idle"
        _send({"type": "error", "text": f"브라우저 오류: {e}"})
        if _session["driver"]:
            try:
                _session["driver"].quit()
            except Exception:
                pass
            _session["driver"] = None
        kill_chrome()


# ── 조회 스레드 ───────────────────────────────────────────────────────────────
def _run_scraper(origin, dest, start_date, end_date):
    driver = _session["driver"]
    try:
        _session["state"] = "searching"
        _send({"type": "log", "text": f"조회 시작: {origin} → {dest}  {start_date} ~ {end_date}", "cls": "info"})

        rows = scrape(
            origin, dest, start_date, end_date,
            driver,
            stop_event=_job["cancel_event"],
            progress_cb=lambda c, t, d: _send({"type": "progress", "current": c, "total": t, "date": d}),
            row_cb=lambda r: _send({"type": "row", "row": r}),
            log_cb=lambda msg, cls: _send({"type": "log", "text": msg, "cls": cls}),
        )

        if rows:
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=[
                "date", "origin", "dest", "flight_no", "dep_time",
                "economy", "business", "upgrade"
            ])
            writer.writeheader()
            writer.writerows(rows)
            _job["csv_data"] = "\ufeff" + buf.getvalue()
            _job["results"]  = rows

        if _job["cancel_event"].is_set():
            _send({"type": "cancelled", "saved": len(rows)})
        else:
            _send({"type": "done", "count": len(rows)})

    except Exception as e:
        _send({"type": "error", "text": str(e)})
    finally:
        _job["running"] = False
        _session["state"] = "ready"   # 조회 완료 후 세션 유지
        _send({"type": "session_ready"})


# ── 라우트 ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/open_browser", methods=["POST"])
def open_browser():
    """브라우저를 열고 로그인 대기 상태로 진입합니다."""
    if _session["state"] not in ("idle",):
        return jsonify({"error": "이미 세션이 존재합니다."}), 400

    _session["login_event"].clear()
    _flush_queue()
    threading.Thread(target=_open_browser, daemon=True).start()
    return jsonify({"status": "opening"})


@app.route("/login_complete", methods=["POST"])
def login_complete():
    _session["login_event"].set()
    return jsonify({"status": "ok"})


@app.route("/start", methods=["POST"])
def start():
    if _session["state"] != "ready":
        return jsonify({"error": "먼저 브라우저를 열고 로그인해 주세요."}), 400
    if _job["running"]:
        return jsonify({"error": "이미 조회 중입니다."}), 400

    data       = request.get_json(force=True)
    origin     = data.get("origin", "").strip().upper()
    dest       = data.get("dest",   "").strip().upper()
    start_date = data.get("start_date", "")
    end_date   = data.get("end_date",   "")

    if not origin or not dest:
        return jsonify({"error": "출발지/도착지를 입력해 주세요."}), 400
    if not start_date or not end_date:
        return jsonify({"error": "날짜를 입력해 주세요."}), 400

    _job["running"] = True
    _job["cancel_event"].clear()
    _job["results"]  = []
    _job["csv_data"] = None
    _flush_queue()

    threading.Thread(
        target=_run_scraper,
        args=(origin, dest, start_date, end_date),
        daemon=True,
    ).start()
    return jsonify({"status": "started"})


@app.route("/cancel", methods=["POST"])
def cancel():
    if not _job["running"]:
        return jsonify({"error": "실행 중인 작업이 없습니다."}), 400
    _job["cancel_event"].set()
    return jsonify({"status": "cancelling"})


@app.route("/quit_browser", methods=["POST"])
def quit_browser():
    """Chrome 세션을 종료합니다."""
    if _job["running"]:
        _job["cancel_event"].set()   # 진행 중이면 취소 먼저

    driver = _session["driver"]
    _session["state"]  = "idle"
    _session["driver"] = None
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
    # Chrome 프로세스도 종료
    kill_chrome()

    _send({"type": "session_closed"})
    return jsonify({"status": "closed"})


@app.route("/session_status")
def session_status():
    return jsonify({"state": _session["state"]})


@app.route("/stream")
def stream():
    def generate():
        while True:
            try:
                msg = _job["msg_queue"].get(timeout=30)
                yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/download")
def download():
    if not _job["csv_data"]:
        return "데이터 없음", 404
    buf = io.BytesIO(_job["csv_data"].encode("utf-8-sig"))
    buf.seek(0)
    return send_file(buf, mimetype="text/csv", as_attachment=True,
                     download_name="asiana_mileage_seats.csv")


def _flush_queue():
    while not _job["msg_queue"].empty():
        try:
            _job["msg_queue"].get_nowait()
        except queue.Empty:
            break


if __name__ == "__main__":
    print("=" * 55)
    print("  아시아나 마일리지 좌석 조회 - 웹 서버")
    print("  http://localhost:5000")
    print("=" * 55)
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
