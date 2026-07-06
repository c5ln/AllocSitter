import argparse
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/memory-allocator-matplotlib")

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd


DEFAULT_METRICS = [
    "mean_sec",
    "median_sec",
    "min_sec",
    "max_sec",
    "mbps",
    "peak_rss_kb",
]

PERFORMANCE_METRICS = ["median_sec", "mean_sec", "mbps", "peak_rss_kb"]
RATIO_METRICS = ["mean_sec", "median_sec", "min_sec", "max_sec", "mbps", "peak_rss_kb"]
SWEEP_METRICS = ["median_sec", "mbps", "peak_rss_kb"]

# allocator 내부 통계. 커스텀 allocator에서만 수집되므로 (default 행은 전부 0)
# 파생 지표는 0-나눗셈을 NaN으로 만들어 default 행을 자연스럽게 제외한다.
ALLOC_RAW_COLS = [
    "malloc_cnt", "calloc_cnt", "realloc_cnt", "free_cnt",
    "req_bytes", "real_bytes", "scan_steps", "remove_steps", "freelist_len",
]
ALLOC_METRICS = ["scan_per_alloc", "remove_per_free", "real_per_req", "freelist_len"]
ALLOC_SWEEP_METRICS = ["scan_per_alloc", "remove_per_free", "freelist_len"]
CALL_KINDS = ["malloc_cnt", "calloc_cnt", "realloc_cnt", "free_cnt"]

LABELS = {
    "mean_sec": "Mean parse time",
    "median_sec": "Median parse time",
    "min_sec": "Best parse time",
    "max_sec": "Worst parse time",
    "mbps": "Throughput",
    "peak_rss_kb": "Peak RSS",
    "scan_per_alloc": "Free-list scan depth (malloc)",
    "remove_per_free": "Free-list scan depth (remove)",
    "real_per_req": "Allocated vs requested bytes",
    "freelist_len": "Free list length (run end)",
}

UNITS = {
    "mean_sec": "seconds",
    "median_sec": "seconds",
    "min_sec": "seconds",
    "max_sec": "seconds",
    "mbps": "MB/s",
    "peak_rss_kb": "KiB",
    "scan_per_alloc": "steps per alloc call",
    "remove_per_free": "steps per free call",
    "real_per_req": "ratio",
    "freelist_len": "blocks",
}

# 검증된 categorical 팔레트 (blue/orange: CVD ΔE 96.7, 대비 >= 3:1)
COLORS = {
    "default": "#2a78d6",
    "mmap-arena": "#eb6834",
}
FALLBACK_COLOR = "#4a3aa7"


def apply_style():
    plt.rcParams.update({
        "figure.facecolor": "white",
        "axes.facecolor": "#fcfcfb",
        "axes.edgecolor": "#cbd5e1",
        "axes.labelcolor": "#334155",
        "axes.titlecolor": "#0f172a",
        "xtick.color": "#475569",
        "ytick.color": "#475569",
        "grid.color": "#cbd5e1",
        "font.size": 10,
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
    })


def short_number(value, _pos=None):
    if pd.isna(value):
        return ""
    sign = "-" if value < 0 else ""
    value = abs(float(value))
    if value >= 1_000_000_000:
        return f"{sign}{value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"{sign}{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{sign}{value / 1_000:.1f}K"
    if value >= 10:
        return f"{sign}{value:.0f}"
    if value >= 1:
        return f"{sign}{value:.2f}"
    return f"{sign}{value:.3f}"


def metric_title(metric):
    return LABELS.get(metric, metric.replace("_", " ").title())


def metric_ylabel(metric):
    unit = UNITS.get(metric)
    return f"{metric_title(metric)} ({unit})" if unit else metric_title(metric)


def series_color(name):
    return COLORS.get(name, FALLBACK_COLOR)


def read_inputs(paths):
    frames = []
    for path in paths:
        p = Path(path)
        if p.is_dir():
            csvs = sorted(p.glob("*.csv"))
        else:
            csvs = [p]

        for csv in csvs:
            frame = pd.read_csv(csv)
            frame["source_csv"] = str(csv)
            frames.append(frame)

    if not frames:
        raise SystemExit("no CSV inputs found")
    return pd.concat(frames, ignore_index=True)


def numeric_metrics(df, requested):
    present = []
    for metric in requested:
        if metric in df.columns:
            df[metric] = pd.to_numeric(df[metric], errors="coerce")
            present.append(metric)
        else:
            print(f"skip missing metric: {metric}")
    if not present:
        raise SystemExit("none of the requested metrics exist in the input CSV")
    return present


def summarize(df, metrics, keys):
    grouped = df.groupby(keys, as_index=False)[metrics].mean(numeric_only=True)
    return grouped.sort_values(keys)


# 외부 반복으로 allocator당 행이 여러 개 → mean 막대 + min-max 에러 바.
# 에러 바가 겹치면 "차이 있음" 결론을 유보해야 한다는 신호.
def metric_spread(df, metric):
    agg = df.groupby("allocator")[metric].agg(["mean", "min", "max"])
    agg = agg.sort_index()
    yerr = [
        (agg["mean"] - agg["min"]).clip(lower=0).to_numpy(),
        (agg["max"] - agg["mean"]).clip(lower=0).to_numpy(),
    ]
    return agg, yerr


def draw_metric_bars(ax, df, metric):
    agg, yerr = metric_spread(df, metric)
    names = list(agg.index)
    colors = [series_color(n) for n in names]
    width = 0.4 if len(names) == 1 else 0.58
    bars = ax.bar(names, agg["mean"], color=colors, width=width,
                  yerr=yerr, capsize=5, error_kw={"color": "#334155", "linewidth": 1.2})
    ax.set_title(metric_title(metric))
    ax.set_ylabel(metric_ylabel(metric))
    ax.grid(axis="y", alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(short_number))
    labels = [short_number(v) for v in agg["mean"]]
    ax.bar_label(bars, labels=labels, padding=8, fontsize=9, color="#334155")
    # 값 라벨이 제목과 겹치지 않게 위쪽 여유 확보
    ax.margins(y=0.15)
    ax.set_ylim(bottom=0)
    if len(names) == 1:
        ax.set_xlim(-1, 1)


def save_metric_bars(df, metric, out_dir):
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    draw_metric_bars(ax, df, metric)
    ax.set_xlabel("Allocator")
    fig.tight_layout()
    fig.savefig(out_dir / f"{metric}.png", dpi=160)
    plt.close(fig)


def save_overview(df, metrics, out_dir):
    selected = [m for m in PERFORMANCE_METRICS if m in metrics]
    if not selected:
        selected = metrics[:2]
    count = len(selected)
    cols = 2
    rows = (count + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(12, max(4.5, rows * 3.5)))
    axes = list(axes.flat) if hasattr(axes, "flat") else [axes]

    for ax, metric in zip(axes, selected):
        draw_metric_bars(ax, df, metric)

    for ax in axes[len(selected):]:
        ax.axis("off")

    fig.suptitle("Tree-sitter Redis Parsing Benchmark", fontsize=15, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "overview.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


def save_speed_comparison(summary, out_dir):
    if {"default", "mmap-arena"} - set(summary["allocator"]):
        return
    if "mean_sec" not in summary.columns or "mbps" not in summary.columns:
        return

    default = summary[summary["allocator"] == "default"].iloc[0]
    mmap = summary[summary["allocator"] == "mmap-arena"].iloc[0]
    slowdown = mmap["mean_sec"] / default["mean_sec"] if default["mean_sec"] else None
    throughput_ratio = mmap["mbps"] / default["mbps"] if default["mbps"] else None

    fig, ax = plt.subplots(figsize=(7.5, 3.8))
    names = ["Parse time\nlower is better", "Throughput\nhigher is better"]
    values = [slowdown, throughput_ratio]
    bars = ax.bar(names, values, color=series_color("mmap-arena"), width=0.5)
    ax.axhline(1.0, color="#334155", linewidth=1, linestyle="--")
    ax.set_title("mmap-arena vs default")
    ax.set_ylabel("Ratio")
    ax.grid(axis="y", alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, pos: f"{v:.2f}x"))
    ax.bar_label(bars, labels=[f"{v:.2f}x" for v in values], padding=3, fontsize=10, color="#334155")
    fig.tight_layout()
    fig.savefig(out_dir / "speed_ratio.png", dpi=180)
    plt.close(fig)


def save_ratio_table(summary, out_dir, keys):
    if "default" not in set(summary["allocator"]):
        return

    group_cols = [k for k in keys if k != "allocator"]
    rows = []
    for _, chunk in summary.groupby(group_cols) if group_cols else [((), summary)]:
        base_rows = chunk[chunk["allocator"] == "default"]
        if base_rows.empty:
            continue
        base = base_rows.iloc[0]
        for _, row in chunk.iterrows():
            if row["allocator"] == "default":
                continue
            item = {k: row[k] for k in keys}
            for col in chunk.columns:
                if col in keys or col not in RATIO_METRICS:
                    continue
                denom = base[col]
                item[f"{col}_vs_default"] = row[col] / denom if denom else None
            rows.append(item)

    if rows:
        pd.DataFrame(rows).to_csv(out_dir / "ratios_vs_default.csv", index=False)


# 스윕 모드: x=입력 크기(bytes), allocator별 라인 + min-max 밴드.
# 스케일링 특성(first-fit이 입력 커질수록 벌어지는가)은 막대가 아니라 곡선으로 봐야 한다.
def save_sweep_lines(df, metric, out_dir):
    agg = (df.groupby(["allocator", "bytes"])[metric]
             .agg(["mean", "min", "max"]).reset_index())

    fig, ax = plt.subplots(figsize=(8, 4.8))
    for alloc in sorted(agg["allocator"].unique()):
        sub = agg[agg["allocator"] == alloc].sort_values("bytes")
        color = series_color(alloc)
        ax.plot(sub["bytes"], sub["mean"], marker="o", markersize=6,
                linewidth=2, color=color, label=alloc)
        ax.fill_between(sub["bytes"], sub["min"], sub["max"], color=color, alpha=0.15)
        last = sub.iloc[-1]
        ax.annotate(alloc, (last["bytes"], last["mean"]),
                    xytext=(6, 0), textcoords="offset points",
                    fontsize=9, color="#334155", va="center")

    ax.set_title(f"{metric_title(metric)} vs input size")
    ax.set_xlabel("Input size (bytes)")
    ax.set_ylabel(metric_ylabel(metric))
    ax.grid(alpha=0.25)
    ax.xaxis.set_major_formatter(FuncFormatter(short_number))
    ax.yaxis.set_major_formatter(FuncFormatter(short_number))
    ax.margins(x=0.12)
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / f"sweep_{metric}.png", dpi=160)
    plt.close(fig)


# CSV의 원시 카운트를 판단용 파생 지표로 변환한다.
# 합계는 iteration 수에 비례해 커지므로 호출당 평균이 비교 가능한 단위다.
def add_alloc_metrics(df):
    if not all(col in df.columns for col in ALLOC_RAW_COLS):
        return []
    for col in ALLOC_RAW_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    alloc_calls = df["malloc_cnt"] + df["calloc_cnt"] + df["realloc_cnt"]
    df["scan_per_alloc"] = df["scan_steps"].div(alloc_calls).where(alloc_calls > 0)
    df["remove_per_free"] = df["remove_steps"].div(df["free_cnt"]).where(df["free_cnt"] > 0)
    df["real_per_req"] = df["real_bytes"].div(df["req_bytes"]).where(df["req_bytes"] > 0)
    # freelist_len은 default 행에서 0(미수집)이라 NaN 처리해 집계에서 제외
    df["freelist_len"] = df["freelist_len"].where(alloc_calls > 0)
    return ALLOC_METRICS


def alloc_rows(df):
    return df[df["scan_per_alloc"].notna()]


# 핵심 질문 "어느 탐색이 병목인가"에 답하는 패널: 두 탐색 깊이는 단위가 같으므로
# (steps/call) 한 축에서 직접 비교한다.
def draw_scan_depths(ax, sub):
    metrics = ["scan_per_alloc", "remove_per_free"]
    kind_labels = ["malloc scan", "remove scan"]
    agg = sub.groupby("allocator")[metrics].agg(["mean", "min", "max"]).sort_index()
    series = list(agg.index)
    bar_w = 0.6 / len(series)

    for i, alloc in enumerate(series):
        xs = [x + (i - (len(series) - 1) / 2) * bar_w for x in range(len(metrics))]
        means = [agg.loc[alloc, (m, "mean")] for m in metrics]
        yerr = [
            [max(0.0, agg.loc[alloc, (m, "mean")] - agg.loc[alloc, (m, "min")]) for m in metrics],
            [max(0.0, agg.loc[alloc, (m, "max")] - agg.loc[alloc, (m, "mean")]) for m in metrics],
        ]
        bars = ax.bar(xs, means, width=bar_w * 0.92, color=series_color(alloc),
                      yerr=yerr, capsize=5, label=alloc,
                      error_kw={"color": "#334155", "linewidth": 1.2})
        ax.bar_label(bars, labels=[short_number(v) for v in means],
                     padding=8, fontsize=9, color="#334155")

    ax.set_xticks(range(len(metrics)))
    ax.set_xticklabels(kind_labels)
    ax.set_title("Free-list scan depth")
    ax.set_ylabel("Steps per call")
    ax.grid(axis="y", alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(short_number))
    ax.margins(y=0.15)
    ax.set_ylim(bottom=0)
    if len(series) >= 2:
        ax.legend(frameon=False)


def save_alloc_overview(df, out_dir):
    sub = alloc_rows(df)
    if sub.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    draw_scan_depths(axes[0], sub)
    draw_metric_bars(axes[1], sub, "real_per_req")
    axes[1].axhline(1.0, color="#334155", linewidth=1, linestyle="--")
    draw_metric_bars(axes[2], sub, "freelist_len")
    fig.suptitle("Custom Allocator Internals (measured iterations)",
                 fontsize=15, fontweight="bold", y=1.04)
    fig.tight_layout()
    fig.savefig(out_dir / "alloc_stats.png", dpi=160, bbox_inches="tight")
    plt.close(fig)


# 호출 종류별 횟수. malloc/free가 지배적이라는 사실 자체가 정보라 linear 축 유지,
# 작은 막대는 직접 라벨로 읽는다.
def save_call_mix(df, out_dir):
    sub = alloc_rows(df)
    if sub.empty:
        return
    agg = sub.groupby("allocator")[CALL_KINDS].mean()
    names = [k.removesuffix("_cnt") for k in CALL_KINDS]
    series = list(agg.index)
    group_w = 0.7
    bar_w = group_w / len(series)

    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    for i, alloc in enumerate(series):
        xs = [x + (i - (len(series) - 1) / 2) * bar_w for x in range(len(CALL_KINDS))]
        values = agg.loc[alloc, CALL_KINDS]
        bars = ax.bar(xs, values, width=bar_w * 0.92, color=series_color(alloc), label=alloc)
        ax.bar_label(bars, labels=[short_number(v) for v in values],
                     padding=4, fontsize=9, color="#334155")
    ax.set_xticks(range(len(CALL_KINDS)))
    ax.set_xticklabels(names)
    ax.set_title("Allocation calls by kind")
    ax.set_ylabel("Calls (mean per run)")
    ax.grid(axis="y", alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(short_number))
    if len(series) >= 2:
        ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / "alloc_call_mix.png", dpi=160)
    plt.close(fig)


# CSV의 size_hist 컬럼("버킷:횟수;버킷:횟수;...")을 dict로 파싱
def parse_size_hist(value):
    if not isinstance(value, str) or not value.strip():
        return {}
    hist = {}
    for part in value.split(";"):
        bucket, _, count = part.partition(":")
        hist[int(bucket)] = hist.get(int(bucket), 0) + float(count)
    return hist


def fmt_bytes(n):
    if n >= 1 << 20:
        return f"{n / (1 << 20):.0f}M"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.0f}K"
    return str(int(n))


def bucket_label(b):
    lo = 0 if b == 0 else 1 << b
    hi = (1 << (b + 1)) - 1
    return f"{fmt_bytes(lo)}–{fmt_bytes(hi)}"


# 요청 size 분포. 한 버킷이 지배적이라는 사실 자체가 결론이므로 linear 축을 유지하고
# 꼬리 버킷은 직접 라벨로 읽는다.
def save_size_hist(df, out_dir):
    if "size_hist" not in df.columns:
        return

    sums, runs = {}, {}
    for _, row in df.iterrows():
        hist = parse_size_hist(row["size_hist"])
        if not hist:
            continue
        alloc = row["allocator"]
        acc = sums.setdefault(alloc, {})
        for bucket, count in hist.items():
            acc[bucket] = acc.get(bucket, 0) + count
        runs[alloc] = runs.get(alloc, 0) + 1
    if not sums:
        return

    buckets = sorted({b for acc in sums.values() for b in acc})
    series = sorted(sums)
    bar_w = 0.7 / len(series)

    fig, ax = plt.subplots(figsize=(max(7.5, 1.1 * len(buckets)), 4.8))
    for i, alloc in enumerate(series):
        means = [sums[alloc].get(b, 0) / runs[alloc] for b in buckets]
        xs = [x + (i - (len(series) - 1) / 2) * bar_w for x in range(len(buckets))]
        bars = ax.bar(xs, means, width=bar_w * 0.92, color=series_color(alloc), label=alloc)
        ax.bar_label(bars, labels=[short_number(v) if v else "" for v in means],
                     padding=4, fontsize=9, color="#334155")
    ax.set_xticks(range(len(buckets)))
    ax.set_xticklabels([bucket_label(b) for b in buckets], rotation=30, ha="right")
    ax.set_title("Request size distribution (log2 buckets)")
    ax.set_xlabel("Requested size (bytes)")
    ax.set_ylabel("Requests (mean per run)")
    ax.grid(axis="y", alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(short_number))
    ax.margins(y=0.15)
    ax.set_ylim(bottom=0)
    if len(series) >= 2:
        ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / "size_hist.png", dpi=160)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="Plot allocator benchmark CSV results.")
    parser.add_argument(
        "inputs",
        nargs="*",
        default=["bench/results"],
        help="CSV files or directories containing CSV files",
    )
    parser.add_argument(
        "-o",
        "--out-dir",
        default="bench/results/plots",
        help="directory where PNG plots and summary CSVs are written",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=DEFAULT_METRICS,
        help="CSV metric columns to plot",
    )
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    apply_style()

    df = read_inputs(args.inputs)
    metrics = numeric_metrics(df, args.metrics)
    alloc_metrics = add_alloc_metrics(df)

    # bytes가 여러 값이면 스윕 데이터: 크기별로 묶지 않으면 서로 다른
    # workload의 평균이 섞여 무의미해진다.
    is_sweep = "bytes" in df.columns and df["bytes"].nunique() > 1
    keys = ["allocator", "bytes"] if is_sweep else ["allocator"]

    # 호출 카운트는 summary에도 포함 (파생 지표의 분모 확인용)
    call_cols = [c for c in CALL_KINDS if c in df.columns] if alloc_metrics else []
    summary = summarize(df, metrics + alloc_metrics + call_cols, keys)
    summary.to_csv(out_dir / "summary.csv", index=False)
    save_ratio_table(summary, out_dir, keys)

    if is_sweep:
        for metric in SWEEP_METRICS:
            if metric in metrics:
                save_sweep_lines(df, metric, out_dir)
        for metric in ALLOC_SWEEP_METRICS:
            if metric in alloc_metrics:
                sub = alloc_rows(df)
                if not sub.empty:
                    save_sweep_lines(sub, metric, out_dir)
    else:
        save_overview(df, metrics, out_dir)
        save_speed_comparison(summary, out_dir)
        for metric in metrics:
            save_metric_bars(df, metric, out_dir)
        if alloc_metrics:
            save_alloc_overview(df, out_dir)
            save_call_mix(df, out_dir)
            save_size_hist(df, out_dir)

    print(f"wrote plots to {out_dir}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
