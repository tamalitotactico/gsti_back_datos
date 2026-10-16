"""Calendario de avisos EMA: muestreo aleatorio estratificado dentro de la ventana diaria.

La ventana [inicio, fin) de cada día se divide en N tramos iguales y se sortea una hora
dentro de cada tramo. Así los avisos no son predecibles pero tampoco se amontonan.
Se dejan 15 minutos de separación mínima respecto al borde de cada tramo.
"""
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def build_schedule(
    start_date: date, days: int, per_day: int, start_hour: int, end_hour: int,
    expiry_minutes: int, tz_name: str, seed: int | None = None,
) -> list[tuple[datetime, datetime]]:
    if end_hour <= start_hour:
        raise ValueError("window_end_hour debe ser mayor que window_start_hour")
    window_min = (end_hour - start_hour) * 60
    segment = window_min / per_day
    margin = min(15, segment / 4)
    if segment - 2 * margin < 1:
        raise ValueError("Demasiados avisos para la ventana indicada")

    rng = random.Random(seed)
    tz = ZoneInfo(tz_name)
    out: list[tuple[datetime, datetime]] = []
    for d in range(days):
        day = start_date + timedelta(days=d)
        base = datetime.combine(day, time(hour=start_hour), tzinfo=tz)
        for k in range(per_day):
            offset = k * segment + margin + rng.random() * (segment - 2 * margin)
            at = (base + timedelta(minutes=offset)).replace(second=0, microsecond=0)
            out.append((at, at + timedelta(minutes=expiry_minutes)))
    return out
