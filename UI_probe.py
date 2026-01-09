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
BUCKETED_REPORT_FILE = "bucketed_p90_report.csv"
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
    url_timings = defaultdict(list)
    bucketed_timings = defaultdict(lambda: defaultdict(list))

    with open(RAW_RESULTS_FILE, "w", newline="") as raw_file:
        raw_writer = csv.writer(raw_file)
        raw_writer.writerow(["timestamp_utc", "url", "page_load_time_ms"])

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()

            while time.time() < end_time:
                for url in urls:
                    if time.time() >= end_time:
                        break

                    page = context.new_page()
                    start = time.time()
                    load_time_ms = -1
                    now = datetime.utcnow()

                    try:
                        page.goto(url, wait_until="load", timeout=60000)
                        load_time_ms = int((time.time() - start) * 1000)
                    except Exception:
                        pass  # keep load_time_ms = -1 for failures

                    # Write raw sample
                    raw_writer.writerow([
                        now.isoformat(),
                        url,
                        load_time_ms
                    ])
                    raw_file.flush()

                    # Store successful samples
                    if load_time_ms > 0:
                        url_timings[url].append(load_time_ms)

                        bucket = get_time_bucket(now)
                        bucketed_timings[url][bucket].append(load_time_ms)

                    page.close()
                    time.sleep(DELAY_BETWEEN_URLS_SEC)

            browser.close()

    # ---------- Summary Report ----------
    with open(SUMMARY_REPORT_FILE, "w", newline="") as summary_file:
        writer = csv.writer(summary_file)
        writer.writerow([
            "url",
            "avg_time_ms",
            "p90_time_ms",
            "max_time_ms",
            "min_time_ms",
            "sample_count"
        ])

        for url, times in url_timings.items():
            writer.writerow([
                url,
                int(statistics.mean(times)),
                int(percentile(times, 90)),
                max(times),
                min(times),
                len(times)
            ])

    # ---------- Bucketed P90 Report ----------
    with open(BUCKETED_REPORT_FILE, "w", newline="") as bucket_file:
        writer = csv.writer(bucket_file)
        writer.writerow([
            "bucket_start_time_utc",
            "url",
            "p90_time_ms",
            "avg_time_ms",
            "sample_count"
        ])

        for url, buckets in bucketed_timings.items():
            for bucket_start, times in sorted(buckets.items()):
                writer.writerow([
                    bucket_start.isoformat(),
                    url,
                    int(percentile(times, 90)),
                    int(statistics.mean(times)),
                    len(times)
                ])


if __name__ == "__main__":
    main()
