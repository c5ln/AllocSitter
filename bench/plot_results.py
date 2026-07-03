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

LABELS = {
    "mean_sec": "Mean parse time",
    "median_sec": "Median parse time",
    "min_sec": "Best parse time",
    "max_sec": "Worst parse time",
    "mbps": "Throughput",
    "peak_rss_kb": "Peak RSS",
}

UNITS = {
    "mean_sec": "seconds",
    "median_sec": "seconds",
    "min_sec": "seconds",
    "max_sec": "seconds",
    "mbps": "MB/s",
    "peak_rss_kb": "KiB",
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
    bars = ax.bar(names, agg["mean"], color=colors, width=0.58,
                  yerr=yerr, capsize=5, error_kw={"color": "#334155", "linewidth": 1.2})
    ax.set_title(metric_title(metric))
    ax.set_ylabel(metric_ylabel(metric))
    ax.grid(axis="y", alpha=0.25)
    ax.yaxis.set_major_formatter(FuncFormatter(short_number))
    labels = [short_number(v) for v in agg["mean"]]
    ax.bar_label(bars, labels=labels, padding=8, fontsize=9, color="#334155")


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

    # bytes가 여러 값이면 스윕 데이터: 크기별로 묶지 않으면 서로 다른
    # workload의 평균이 섞여 무의미해진다.
    is_sweep = "bytes" in df.columns and df["bytes"].nunique() > 1
    keys = ["allocator", "bytes"] if is_sweep else ["allocator"]

    summary = summarize(df, metrics, keys)
    summary.to_csv(out_dir / "summary.csv", index=False)
    save_ratio_table(summary, out_dir, keys)

    if is_sweep:
        for metric in SWEEP_METRICS:
            if metric in metrics:
                save_sweep_lines(df, metric, out_dir)
    else:
        save_overview(df, metrics, out_dir)
        save_speed_comparison(summary, out_dir)
        for metric in metrics:
            save_metric_bars(df, metric, out_dir)

    print(f"wrote plots to {out_dir}")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
