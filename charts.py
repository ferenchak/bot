"""Генерація графіків через matplotlib для відправки у Telegram як PNG."""

import io
from datetime import date, datetime, timedelta

import matplotlib

matplotlib.use("Agg")  # без GUI, для серверу
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def _setup_style():
    """Однакові базові налаштування для всіх графіків."""
    plt.rcParams.update({
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.titleweight": "bold",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "figure.facecolor": "white",
    })


def _fig_to_bytes(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def chart_sales_by_day(daily_data: list, title: str) -> io.BytesIO:
    """Стовпчиковий графік продажів по днях.
    daily_data: [(date_obj, sales_amount), ...]
    """
    _setup_style()
    fig, ax = plt.subplots(figsize=(10, 4.5))

    if not daily_data:
        ax.text(0.5, 0.5, "Немає даних", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        return _fig_to_bytes(fig)

    dates = [d[0] for d in daily_data]
    values = [d[1] for d in daily_data]

    bars = ax.bar(dates, values, color="#4A90E2", edgecolor="white", linewidth=1)
    # Підсвітимо найкращий день
    if values:
        max_idx = values.index(max(values))
        bars[max_idx].set_color("#27AE60")

    ax.set_title(title)
    ax.set_ylabel("Сума (грн)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=45)

    # Підпис над топ-стовпцем
    if values and max(values) > 0:
        ax.text(
            dates[max_idx], max(values),
            f"  {max(values):,.0f}".replace(",", " "),
            ha="left", va="bottom", fontsize=9, color="#27AE60", fontweight="bold",
        )

    return _fig_to_bytes(fig)


def chart_vc_breakdown(daily_data: list, title: str) -> io.BytesIO:
    """Графік VC vs не-VC по днях (stacked).
    daily_data: [(date_obj, total_amount, vc_amount), ...]
    """
    _setup_style()
    fig, ax = plt.subplots(figsize=(10, 4.5))

    if not daily_data:
        ax.text(0.5, 0.5, "Немає даних", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        return _fig_to_bytes(fig)

    dates = [d[0] for d in daily_data]
    total = [d[1] for d in daily_data]
    vc = [d[2] for d in daily_data]
    other = [t - v for t, v in zip(total, vc)]

    ax.bar(dates, vc, color="#9B59B6", label="VC", edgecolor="white", linewidth=1)
    ax.bar(dates, other, bottom=vc, color="#4A90E2", label="Інші", edgecolor="white", linewidth=1)

    ax.set_title(title)
    ax.set_ylabel("Сума (грн)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=45)

    return _fig_to_bytes(fig)


def chart_direct_activity(daily_data: list, title: str) -> io.BytesIO:
    """Графік Direct: чати vs продажі (двовісний).
    daily_data: [(date_obj, chats, sales_count), ...]
    """
    _setup_style()
    fig, ax1 = plt.subplots(figsize=(10, 4.5))

    if not daily_data:
        ax1.text(0.5, 0.5, "Немає даних", ha="center", va="center", transform=ax1.transAxes)
        ax1.set_title(title)
        return _fig_to_bytes(fig)

    dates = [d[0] for d in daily_data]
    chats = [d[1] for d in daily_data]
    sales = [d[2] for d in daily_data]

    color1 = "#4A90E2"
    ax1.bar(dates, chats, color=color1, alpha=0.7, label="Чати", edgecolor="white", linewidth=1)
    ax1.set_ylabel("Чатів", color=color1)
    ax1.tick_params(axis="y", labelcolor=color1)

    ax2 = ax1.twinx()
    color2 = "#E74C3C"
    ax2.plot(dates, sales, color=color2, marker="o", linewidth=2, label="Продажі")
    ax2.set_ylabel("Продаж", color=color2)
    ax2.tick_params(axis="y", labelcolor=color2)
    ax2.spines["top"].set_visible(False)
    ax2.grid(False)

    ax1.set_title(title)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=45)

    return _fig_to_bytes(fig)


def chart_smm_activity(daily_data: list, title: str) -> io.BytesIO:
    """SMM: сторіз і рілси по днях (group bars).
    daily_data: [(date_obj, stories, reels), ...]
    """
    _setup_style()
    fig, ax = plt.subplots(figsize=(10, 4.5))

    if not daily_data:
        ax.text(0.5, 0.5, "Немає даних", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        return _fig_to_bytes(fig)

    dates = [d[0] for d in daily_data]
    stories = [d[1] for d in daily_data]
    reels = [d[2] for d in daily_data]

    width = 0.35
    x_positions = list(range(len(dates)))
    ax.bar([x - width / 2 for x in x_positions], stories, width, label="Сторіз", color="#F39C12")
    ax.bar([x + width / 2 for x in x_positions], reels, width, label="Рілси/дописи", color="#16A085")

    ax.set_title(title)
    ax.set_ylabel("Кількість")
    ax.set_xticks(x_positions)
    ax.set_xticklabels([d.strftime("%d.%m") for d in dates], rotation=45)
    ax.legend()

    return _fig_to_bytes(fig)


def chart_personal_sales(daily_data: list, title: str, ylabel: str = "Сума (грн)") -> io.BytesIO:
    """Особистий графік продажів по днях."""
    _setup_style()
    fig, ax = plt.subplots(figsize=(10, 4.5))

    if not daily_data:
        ax.text(0.5, 0.5, "Немає даних", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        return _fig_to_bytes(fig)

    dates = [d[0] for d in daily_data]
    values = [d[1] for d in daily_data]

    ax.plot(dates, values, color="#27AE60", marker="o", linewidth=2)
    ax.fill_between(dates, values, alpha=0.2, color="#27AE60")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
    fig.autofmt_xdate(rotation=45)

    return _fig_to_bytes(fig)
