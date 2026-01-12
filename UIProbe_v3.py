
You said:
import time
import csv
import math
import statistics
from datetime import datetime
from collections import defaultdict
from playwright.sync_api import sync_playwright
import pandas as pd

# ================= CONFIG =================
URL_FILE = "urls.txt"          # urls.txt or urls.csv
TEST_DURATION_MINUTES = 30
DELAY_BETWEEN_URLS_SEC = 5
BUCKET_SIZE_MINUTES = 5

RAW_RESULTS_FILE = "results.csv"
SUMMARY_REPORT_FILE = "summary_report.csv"
MERGED_BUCKET_REPORT_FILE = "bucketed_performance_report.csv"
# ==========================================


# ---------- Utility Functions ----------

def load_urls(file_path):
    if file_path.endswith(".csv"):
        return pd.read_csv(file_path)["url"].dropna().tolist()
    else:
        with open(file_path, "r") as f:
            return [line.strip() for line in f if line.strip()]


def percentile(data, pct):
    if not data:
        return -1
    data = sorted(data)
    k = (len(data) - 1) * (pct / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return data[int(k)]
    return data[f] + (data[c] - data[f]) * (k - f)


def get_time_bucket(ts):
    return ts.replace(
        minute=(ts.minute // BUCKET_SIZE_MINUTES) * BUCKET_SIZE_MINUTES,
        second=0,
        microsecond=0
    )


# ---------- Main Logic ----------

def main():
    urls = load_urls(URL_FILE)
    end_time = time.time() + (TEST_DURATION_MINUTES * 60)

    # In-memory stores
    url_load_timings = defaultdict(list)
    bucketed_load_timings = defaultdict(lambda: defaultdict(list))
    bucketed_lcp_timings = defaultdict(lambda: defaultdict(list))

    with open(RAW_RESULTS_FILE, "w", newline="") as raw_file:
        raw_writer = csv.writer(raw_file)
        raw_writer.writerow([
            "timestamp_utc",
            "url",
            "page_load_time_ms",
            "fcp_ms",
            "lcp_ms",
            "cls"
        ])

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()

            while time.time() < end_time:
                for url in urls:
                    if time.time() >= end_time:
                        break

                    page = context.new_page()
                    start = time.time()
                    now = datetime.utcnow()

                    load_time_ms = -1
                    fcp = -1
                    lcp = -1
                    cls = 0.0

                    try:
                        page.goto(url, wait_until="load", timeout=60000)

                        load_time_ms = int((time.time() - start) * 1000)

                        # --------- FCP ----------
                        fcp = page.evaluate("""
                        () => {
                          const e = performance.getEntriesByName('first-contentful-paint')[0];
                          return e ? e.startTime : -1;
                        }
                        """)

                        # --------- LCP ----------
                        lcp = page.evaluate("""
                        () => new Promise(resolve => {
                          new PerformanceObserver((list) => {
                            const entries = list.getEntries();
                            resolve(entries[entries.length - 1].startTime);
                          }).observe({ type: 'largest-contentful-paint', buffered: true });
                        })
                        """)

                        # --------- CLS ----------
                        cls = page.evaluate("""
                        () => {
                          let value = 0;
                          new PerformanceObserver((list) => {
                            for (const e of list.getEntries()) {
                              if (!e.hadRecentInput) value += e.value;
                            }
                          }).observe({ type: 'layout-shift', buffered: true });
                          return value;
                        }
                        """)

                    except Exception:
                        pass

                    # --------- Raw CSV ----------
                    raw_writer.writerow([
                        now.isoformat(),
                        url,
                        load_time_ms,
                        int(fcp),
                        int(lcp),
                        round(cls, 3)
                    ])
                    raw_file.flush()

                    # --------- Store successful samples ----------
                    if load_time_ms > 0:
                        url_load_timings[url].append(load_time_ms)

                        bucket = get_time_bucket(now)
                        bucketed_load_timings[url][bucket].append(load_time_ms)

                        if lcp > 0:
                            bucketed_lcp_timings[url][bucket].append(lcp)

                    page.close()
                    time.sleep(DELAY_BETWEEN_URLS_SEC)

            browser.close()

    # ---------- Summary Report (Load Time) ----------
    with open(SUMMARY_REPORT_FILE, "w", newline="") as summary_file:
        writer = csv.writer(summary_file)
        writer.writerow([
            "url",
            "avg_load_time_ms",
            "p90_load_time_ms",
            "max_load_time_ms",
            "min_load_time_ms",
            "sample_count"
        ])

        for url, times in url_load_timings.items():
            writer.writerow([
                url,
                int(statistics.mean(times)),
                int(percentile(times, 90)),
                max(times),
                min(times),
                len(times)
            ])

    # ---------- Merged Bucketed Performance Report ----------
    with open(MERGED_BUCKET_REPORT_FILE, "w", newline="") as bucket_file:
        writer = csv.writer(bucket_file)
        writer.writerow([
            "bucket_start_time_utc",
            "url",
            "p90_load_time_ms",
            "avg_load_time_ms",
            "p90_lcp_ms",
            "avg_lcp_ms",
            "sample_count"
        ])

        all_urls = set(bucketed_load_timings.keys()) | set(bucketed_lcp_timings.keys())

        for url in all_urls:
            all_buckets = set(bucketed_load_timings[url].keys()) | set(bucketed_lcp_timings[url].keys())

            for bucket_start in sorted(all_buckets):
                load_times = bucketed_load_timings[url].get(bucket_start, [])
                lcp_times = bucketed_lcp_timings[url].get(bucket_start, [])

                writer.writerow([
                    bucket_start.isoformat(),
                    url,
                    int(percentile(load_times, 90)) if load_times else -1,
                    int(statistics.mean(load_times)) if load_times else -1,
                    int(percentile(lcp_times, 90)) if lcp_times else -1,
                    int(statistics.mean(lcp_times)) if lcp_times else -1,
                    max(len(load_times), len(lcp_times))
                ])


if __name__ == "__main__":
    main()
