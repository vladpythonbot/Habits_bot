from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SLEEP_GOAL_HOURS = 8
RATE_LABELS = {
    1: "очень плохо",
    2: "плохо",
    3: "нормально",
    4: "хорошо",
    5: "отлично",
}


@dataclass(frozen=True)
class SleepSummary:
    avg_hours: float | None
    avg_rate: float | None
    avg_sleep_out: str | None
    avg_sleep_up: str | None
    goal_days: int
    recorded_days: int


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _time_to_minutes(value: str | None, bedtime: bool = False) -> int | None:
    if not value:
        return None
    try:
        hours, minutes = [int(part) for part in value.split(":", 1)]
    except (AttributeError, ValueError):
        return None
    total = hours * 60 + minutes
    if bedtime and total < 12 * 60:
        total += 24 * 60
    return total


def _minutes_to_time(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    value = int(round(minutes)) % (24 * 60)
    return f"{value // 60:02d}:{value % 60:02d}"


def _duration_label(hours: float | None) -> str:
    if hours is None:
        return "нет данных"
    total_minutes = int(round(hours * 60))
    return f"{total_minutes // 60} ч {total_minutes % 60:02d} мин"


def sleep_summary(items: list[dict]) -> SleepSummary:
    hour_values = [float(item["hours"]) for item in items if float(item.get("hours") or 0) > 0]
    rate_values = [int(item["rate"]) for item in items if int(item.get("rate") or 0) > 0]
    sleep_out_values = [
        value for value in (_time_to_minutes(item.get("sleep_out"), bedtime=True) for item in items) if value is not None
    ]
    sleep_up_values = [
        value for value in (_time_to_minutes(item.get("sleep_up")) for item in items) if value is not None
    ]

    return SleepSummary(
        avg_hours=sum(hour_values) / len(hour_values) if hour_values else None,
        avg_rate=sum(rate_values) / len(rate_values) if rate_values else None,
        avg_sleep_out=_minutes_to_time(sum(sleep_out_values) / len(sleep_out_values)) if sleep_out_values else None,
        avg_sleep_up=_minutes_to_time(sum(sleep_up_values) / len(sleep_up_values)) if sleep_up_values else None,
        goal_days=sum(1 for value in hour_values if value >= SLEEP_GOAL_HOURS),
        recorded_days=len(hour_values),
    )


def sleep_caption(items: list[dict]) -> str:
    summary = sleep_summary(items)
    if summary.recorded_days == 0 and summary.avg_rate is None:
        return (
            "🌙 <b>Сон за неделю</b>\n\n"
            "Пока нет записей сна. Заполни отбой, подъём и оценку в Mini App или ответь на утренний вопрос бота."
        )

    avg_rate = f"{summary.avg_rate:.1f} / 5" if summary.avg_rate is not None else "нет данных"
    return (
        "🌙 <b>Сон за неделю</b>\n\n"
        f"Средний сон: {_duration_label(summary.avg_hours)}\n"
        f"Средняя оценка: {avg_rate}\n"
        f"Средний отбой: {summary.avg_sleep_out or 'нет данных'}\n"
        f"Средний подъём: {summary.avg_sleep_up or 'нет данных'}\n"
        f"Цель 8 часов: {summary.goal_days}/7 дней"
    )


def _label_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    return f"{weekdays[parsed.weekday()]}\n{parsed.strftime('%d.%m')}"


def _draw_centered(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, font, fill, spacing: int = 4) -> None:
    x, y = xy
    lines = text.splitlines()
    line_heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    total = sum(line_heights) + spacing * (len(lines) - 1)
    top = y - total // 2
    for line, height in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        draw.text((x - (bbox[2] - bbox[0]) // 2, top), line, font=font, fill=fill)
        top += height + spacing


def build_sleep_chart_png(items: list[dict]) -> bytes:
    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), "#0b1220")
    draw = ImageDraw.Draw(image)

    title_font = _font(48, bold=True)
    subtitle_font = _font(25)
    label_font = _font(24, bold=True)
    small_font = _font(21)
    table_font = _font(22)
    table_bold = _font(22, bold=True)

    draw.text((60, 52), "Сон за неделю", font=title_font, fill="#f8fafc")
    draw.text((60, 112), "7 дней · цель 8 часов · оценка 1-5", font=subtitle_font, fill="#93a4bb")

    chart_x, chart_y = 70, 190
    chart_w, chart_h = 940, 540
    draw.rounded_rectangle((chart_x, chart_y, chart_x + chart_w, chart_y + chart_h), radius=34, fill="#111b2e")

    plot_left = chart_x + 78
    plot_top = chart_y + 62
    plot_right = chart_x + chart_w - 42
    plot_bottom = chart_y + chart_h - 96
    plot_w = plot_right - plot_left
    plot_h = plot_bottom - plot_top
    max_hours = 12

    for value in range(0, max_hours + 1, 2):
        y = plot_bottom - value / max_hours * plot_h
        color = "#26364f" if value != SLEEP_GOAL_HOURS else "#facc15"
        draw.line((plot_left, y, plot_right, y), fill=color, width=2 if value != SLEEP_GOAL_HOURS else 4)
        draw.text((chart_x + 28, y - 13), str(value), font=small_font, fill="#64748b")
    draw.text((plot_right - 120, plot_bottom - SLEEP_GOAL_HOURS / max_hours * plot_h - 35), "цель 8ч", font=small_font, fill="#facc15")

    bar_area = plot_w / 7
    bar_w = 58
    for index, item in enumerate(items[:7]):
        hours = max(0, min(max_hours, float(item.get("hours") or 0)))
        rate = int(item.get("rate") or 0)
        center_x = int(plot_left + bar_area * index + bar_area / 2)
        bar_h = int(hours / max_hours * plot_h)
        x1, x2 = center_x - bar_w // 2, center_x + bar_w // 2
        y1, y2 = plot_bottom - bar_h, plot_bottom
        fill = "#60a5fa" if hours >= SLEEP_GOAL_HOURS else "#7c3aed"
        if hours > 0:
            draw.rounded_rectangle((x1, y1, x2, y2), radius=16, fill=fill)
            draw.text((center_x - 32, y1 - 34), f"{hours:.1f}ч", font=small_font, fill="#dbeafe")
        else:
            draw.rounded_rectangle((x1, plot_bottom - 8, x2, plot_bottom), radius=4, fill="#27364e")
            draw.text((center_x - 18, plot_bottom - 38), "-", font=small_font, fill="#64748b")

        rate_text = str(rate) if rate else "-"
        rate_fill = "#22c55e" if rate >= 4 else "#facc15" if rate == 3 else "#fb7185" if rate else "#64748b"
        draw.rounded_rectangle((center_x - 26, plot_top - 44, center_x + 26, plot_top - 4), radius=18, fill="#0b1220", outline=rate_fill, width=2)
        _draw_centered(draw, (center_x, plot_top - 24), rate_text, label_font, rate_fill)
        _draw_centered(draw, (center_x, plot_bottom + 42), _label_date(item.get("sleep_date", item.get("date", ""))), small_font, "#cbd5e1", spacing=2)

    summary = sleep_summary(items)
    cards = [
        ("Средний сон", _duration_label(summary.avg_hours)),
        ("Оценка", f"{summary.avg_rate:.1f}/5" if summary.avg_rate is not None else "-"),
        ("Цель 8ч", f"{summary.goal_days}/7"),
    ]
    card_y = 770
    card_w = 300
    for index, (label, value) in enumerate(cards):
        x = 70 + index * 325
        draw.rounded_rectangle((x, card_y, x + card_w, card_y + 132), radius=28, fill="#111b2e")
        draw.text((x + 28, card_y + 26), label, font=small_font, fill="#93a4bb")
        draw.text((x + 28, card_y + 68), value, font=_font(32, bold=True), fill="#f8fafc")

    draw.text((70, 954), "Детали по дням", font=_font(32, bold=True), fill="#f8fafc")
    headers = ["Дата", "Отбой", "Подъём", "Сон", "Оценка"]
    xs = [78, 250, 405, 580, 790]
    y = 1014
    for x, header in zip(xs, headers):
        draw.text((x, y), header, font=table_bold, fill="#93a4bb")
    y += 42
    for item in items[:7]:
        hours = float(item.get("hours") or 0)
        rate = int(item.get("rate") or 0)
        values = [
            _label_date(item.get("sleep_date", item.get("date", ""))).replace("\n", " "),
            item.get("sleep_out") or "-",
            item.get("sleep_up") or "-",
            _duration_label(hours) if hours > 0 else "-",
            f"{rate} · {RATE_LABELS.get(rate, '-')}" if rate else "-",
        ]
        for x, value in zip(xs, values):
            draw.text((x, y), value, font=table_font, fill="#e2e8f0")
        y += 38

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
