import time
import csv
import math
import statistics
import os
import uuid
import argparse
from datetime import datetime
from collections import defaultdict

import pandas as pd
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ================= CLI =================

def parse_args():
    parser = argparse.ArgumentParser("Synthetic Web Performance Monitor")
    parser.add_argument("--env", default="staging")
    parser.add_argument("--urls", default="urls.txt")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--delay", type=int, default=5)
    parser.add_argument("--bucket", type=int, default=5)
    return parser.parse_args()


# ================= UTILS =================

def load_urls(file_path):
    if file_path.endswith(".csv"):
        return pd.read_csv(file_path)["url"].dropna().tolist()
    with open(file_path) as f:
        return [l.strip() for l in f if l.strip()]


def percentile(data, pct):
    if not data:
        return -1
    data = sorted(data)
    k = (len(data) - 1) * (pct / 100)
    f, c = math.floor(k), math.ceil(k)
    return data[int(k)] if f == c else data[f] + (data[c] - data[f]) * (k - f)


def bucket_time(ts, size):
    return ts.replace(
        minute=(ts.minute // size) * size,
        second=0,
        microsecond=0
    )


def safe_filename(s):
    return s.replace("https://", "").replace("http://", "").replace("/", "_")


# ================= SCENARIO OBSERVER =================

class ScenarioObserver:
    def __init__(self):
        self.start = None

    def begin(self):
        self.start = time.time()

    def end(self, page):
        if not self.start:
            return None

        duration = int((time.time() - self.start) * 1000)

        fcp = page.evaluate("""
            () => performance.getEntriesByName('first-contentful-paint')[0]?.startTime || -1
        """)

        lcp = page.evaluate("""
            () => new Promise(resolve => {
              new PerformanceObserver(list => {
                const e = list.getEntries()
                resolve(e[e.length - 1]?.startTime || -1)
              }).observe({ type: 'largest-contentful-paint', buffered: true })
            })
        """)

        cls = page.evaluate("""
            () => {
              let v = 0
              new PerformanceObserver(list => {
                for (const e of list.getEntries()) {
                  if (!e.hadRecentInput) v += e.value
                }
              }).observe({ type: 'layout-shift', buffered: true })
              return v
            }
        """)

        self.start = None
        return duration, fcp, lcp, cls


# ================= MAIN =================

def main():
    args = parse_args()

    RUN_ID = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
    BASE = os.path.join("runs", args.env, RUN_ID)
    SHOTS = os.path.join(BASE, "screenshots")

    os.makedirs(SHOTS, exist_ok=True)

    RAW = os.path.join(BASE, "results.csv")
    ERR = os.path.join(BASE, "errors.csv")
    SUM = os.path.join(BASE, "summary_report.csv")
    BUCKET = os.path.join(BASE, "bucketed_performance_report.csv")
    PROM = os.path.join(BASE, "prometheus_metrics.txt")

    urls = load_urls(args.urls)
    end = time.time() + args.duration * 60

    timings = defaultdict(list)
    bucketed = defaultdict(lambda: defaultdict(list))
    bucketed_lcp = defaultdict(lambda: defaultdict(list))

    observer = ScenarioObserver()

    with open(RAW, "w", newline="") as rf, open(ERR, "w", newline="") as ef:
        rw, ew = csv.writer(rf), csv.writer(ef)

        rw.writerow([
            "timestamp_utc", "env", "run_id", "url", "status",
            "duration_ms", "fcp_ms", "lcp_ms", "cls",
            "error_type", "error_message", "screenshot"
        ])

        ew.writerow([
            "timestamp_utc", "env", "run_id", "url",
            "error_type", "error_message", "screenshot"
        ])

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context()

            while time.time() < end:
                for url in urls:
                    if time.time() >= end:
                        break

                    page = ctx.new_page()
                    now = datetime.utcnow()

                    status = "SUCCESS"
                    err_t = err_m = shot = ""
                    duration = fcp = lcp = -1
                    cls = 0.0

                    try:
                        observer.begin()
                        page.goto(url, timeout=60000, wait_until="load")
                        duration, fcp, lcp, cls = observer.end(page)

                    except PlaywrightTimeoutError as e:
                        status, err_t, err_m = "FAILURE", "TIMEOUT", str(e)

                    except Exception as e:
                        status, err_t, err_m = "FAILURE", "ERROR", str(e)

                    if status != "SUCCESS":
                        shot = os.path.join(
                            SHOTS,
                            f"{now.strftime('%H%M%S')}_{safe_filename(url)}_{err_t}.png"
                        )
                        try:
                            page.screenshot(path=shot, full_page=True)
                        except:
                            shot = ""

                        ew.writerow([now.isoformat(), args.env, RUN_ID, url, err_t, err_m, shot])

                    rw.writerow([
                        now.isoformat(), args.env, RUN_ID, url, status,
                        duration, int(fcp), int(lcp), round(cls, 3),
                        err_t, err_m, shot
                    ])

                    if status == "SUCCESS" and duration > 0:
                        timings[url].append(duration)
                        b = bucket_time(now, args.bucket)
                        bucketed[url][b].append(duration)
                        if lcp > 0:
                            bucketed_lcp[url][b].append(lcp)

                    page.close()
                    time.sleep(args.delay)

            browser.close()

    # ===== SUMMARY =====
    with open(SUM, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["url", "avg_ms", "p90_ms", "max", "min", "samples"])
        for u, t in timings.items():
            w.writerow([
                u,
                int(statistics.mean(t)),
                int(percentile(t, 90)),
                max(t), min(t), len(t)
            ])

    # ===== BUCKET =====
    with open(BUCKET, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "bucket_start_utc", "env", "run_id", "url",
            "p90_load_ms", "avg_load_ms",
            "p90_lcp_ms", "avg_lcp_ms", "samples"
        ])

        for u in bucketed:
            for b in bucketed[u]:
                lt = bucketed[u][b]
                lc = bucketed_lcp[u].get(b, [])
                w.writerow([
                    b.isoformat(), args.env, RUN_ID, u,
                    int(percentile(lt, 90)),
                    int(statistics.mean(lt)),
                    int(percentile(lc, 90)) if lc else -1,
                    int(statistics.mean(lc)) if lc else -1,
                    len(lt)
                ])

    # ===== PROM =====
    with open(PROM, "w") as f:
        for u, t in timings.items():
            f.write(
                f'web_page_load_p90_ms{{env="{args.env}",url="{u}",run_id="{RUN_ID}"}} '
                f'{int(percentile(t, 90))}\n'
            )


if __name__ == "__main__":
    main()
