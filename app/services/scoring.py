"""Validación y puntuación de instrumentos psicométricos y EMA."""
from dataclasses import dataclass, field

from app.catalog import BHS_FALSE_KEYED, InstrumentDef


class ScoringError(ValueError):
    """Respuesta inválida: ítems desconocidos, fuera de rango o incompletos."""


@dataclass
class ScoreResult:
    values: dict[int, int]  # valores efectivos (con saltos completados en 0)
    total: float | None
    subscores: dict = field(default_factory=dict)


def _skippable(ins: InstrumentDef, given: dict[int, int]) -> set[int]:
    """Ítems que el flujo del cuestionario permite omitir según respuestas previas."""
    if ins.code == "CSSRS_SCREEN":
        skip: set[int] = set()
        if given.get(2) == 0:
            skip |= {3, 4, 5}  # sin pensamientos activos no se pregunta método/intención/plan
        if given.get(6) == 0:
            skip.add(7)  # sin conducta en la vida no se pregunta por los últimos 3 meses
        return skip
    if ins.code == "BSS":
        skip = {20, 21}  # no puntuados
        if given.get(4) == 0 and given.get(5) == 0:
            skip |= set(range(6, 20))  # tamizaje de la BSS: ítems 6-19 se omiten
        return skip
    return set()


def validate_and_score(ins: InstrumentDef, raw: dict[int, int]) -> ScoreResult:
    items = {it.item_no: it for it in ins.items}

    unknown = sorted(set(raw) - set(items))
    if unknown:
        raise ScoringError(f"Ítems inexistentes en {ins.code}: {unknown}")
    for no, v in raw.items():
        it = items[no]
        if not it.min_value <= v <= it.max_value:
            raise ScoringError(f"{ins.code} ítem {no}: valor {v} fuera de rango [{it.min_value}, {it.max_value}]")

    missing = sorted(set(items) - set(raw) - _skippable(ins, raw))
    if missing:
        raise ScoringError(f"Faltan ítems obligatorios en {ins.code}: {missing}")

    values = {no: raw.get(no, 0) for no in items}

    if ins.code == "CSSRS_SCREEN" and values[7] == 1 and values[6] == 0:
        raise ScoringError("CSSRS_SCREEN: conducta en 3 meses (ítem 7) requiere conducta en la vida (ítem 6)")

    scored = [values[no] for no, it in items.items() if it.scored]
    sub: dict = {}
    total: float | None

    match ins.scoring:
        case "sum":
            total = float(sum(scored))
        case "mean":
            total = round(sum(scored) / len(scored), 3)
        case "bhs":
            total = float(sum(1 for no, v in values.items() if (v == 0) == (no in BHS_FALSE_KEYED)))
        case "bss":
            total = float(sum(values[no] for no in range(1, 20)))
            sub = {"screen_items_4_5": values[4] + values[5], "skipped_6_19": all(n not in raw for n in range(6, 20))}
        case "cssrs":
            level = max((no for no in range(1, 6) if values[no] == 1), default=0)
            total = float(level)
            sub = {"ideation_level": level, "behavior_lifetime": values[6], "behavior_3m": values[7]}
        case "sus":
            odd = sum(values[no] - 1 for no in (1, 3, 5, 7, 9))
            even = sum(5 - values[no] for no in (2, 4, 6, 8, 10))
            total = (odd + even) * 2.5
        case "none":
            total = None
            sub = {it.code: values[no] for no, it in items.items()}
        case _:
            raise ScoringError(f"Regla de puntuación desconocida: {ins.scoring}")

    return ScoreResult(values=values, total=total, subscores=sub)
