"""Catálogo de métricas de sensores e instrumentos psicométricos.

Se carga en la base de datos por la migración 0002. Solo contiene números de
ítem, rangos y reglas de puntuación: los textos de los ítems los muestra la app
móvil y están sujetos a la licencia de cada instrumento (BHS y BSS son de Pearson).
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class MetricDef:
    code: str
    description: str
    unit: str
    value_type: str  # count | minutes | binary | continuous
    granularity: str  # 30min | night | day | sample
    min_value: float
    max_value: float
    daily_agg: str  # sum | mean | max


METRICS: list[MetricDef] = [
    # Indicadores comportamentales (matriz de operacionalización)
    MetricDef("steps", "Pasos por bloque de 30 min", "pasos", "count", "30min", 0, 8000, "sum"),
    MetricDef("sleep_minutes", "Minutos totales dormidos por noche", "min", "minutes", "night", 0, 1080, "sum"),
    MetricDef("at_home", "1 si estuvo en casa en algún momento del bloque de 30 min (casa inferida en el dispositivo)",
              "binario", "binary", "30min", 0, 1, "sum"),
    MetricDef("app_use", "1 si usó alguna app en el bloque de 30 min", "binario", "binary", "30min", 0, 1, "sum"),
    # Bioseñales del wearable (opcionales según el modelo de reloj)
    MetricDef("heart_rate_mean", "Frecuencia cardiaca media del bloque", "lpm", "continuous", "30min", 25, 230, "mean"),
    MetricDef("resting_heart_rate", "Frecuencia cardiaca en reposo diaria", "lpm", "continuous", "day", 25, 150, "mean"),
    MetricDef("hrv_rmssd_night", "HRV nocturna (RMSSD)", "ms", "continuous", "night", 1, 300, "mean"),
    MetricDef("spo2_night_mean", "SpO2 nocturna media", "%", "continuous", "night", 50, 100, "mean"),
]


@dataclass(frozen=True)
class ItemDef:
    item_no: int
    code: str
    min_value: int
    max_value: int
    scored: bool = True
    is_safety_item: bool = False


@dataclass(frozen=True)
class InstrumentDef:
    code: str
    name: str
    version: str
    scoring: str  # sum | mean | bhs | bss | cssrs | sus | none
    purpose: str  # predictor | reference | safety | usability | ema
    administration: str
    items: list[ItemDef] = field(default_factory=list)

    @property
    def n_items(self) -> int:
        return len(self.items)


def _likert(prefix: str, n: int, lo: int, hi: int, safety: set[int] | None = None) -> list[ItemDef]:
    safety = safety or set()
    return [ItemDef(i, f"{prefix}_{i}", lo, hi, True, i in safety) for i in range(1, n + 1)]


INSTRUMENTS: list[InstrumentDef] = [
    # Bloque de autoinforme del chatbot según el artículo (3.10): PHQ-9, GAD-7, ULS-6 y C-SSRS.
    # La etiqueta de referencia (ground truth) se obtiene de C-SSRS + ítem 9 del PHQ-9.
    InstrumentDef(
        "CSSRS_SCREEN", "Columbia Suicide Severity Rating Scale - Screener", "screen-recent", "cssrs", "reference", "weekly",
        [
            ItemDef(1, "wish_dead", 0, 1, is_safety_item=True),
            ItemDef(2, "active_thoughts", 0, 1, is_safety_item=True),
            ItemDef(3, "method", 0, 1, is_safety_item=True),
            ItemDef(4, "intent", 0, 1, is_safety_item=True),
            ItemDef(5, "plan", 0, 1, is_safety_item=True),
            ItemDef(6, "behavior_lifetime", 0, 1, is_safety_item=True),
            ItemDef(7, "behavior_3m", 0, 1, is_safety_item=True),
        ],
    ),
    InstrumentDef("NSSI", "Autolesión no suicida (ítem único)", "1", "sum", "safety", "weekly",
                  [ItemDef(1, "nssi_recent", 0, 1, is_safety_item=True)]),
    InstrumentDef("PHQ9", "Patient Health Questionnaire-9", "1", "sum", "predictor", "baseline",
                  _likert("phq9", 9, 0, 3, safety={9})),
    # Instrumentos complementarios (opcionales; no forman parte del modelo del artículo)
    InstrumentDef(
        "BSS", "Beck Scale for Suicide Ideation", "1", "bss", "safety", "weekly",
        [ItemDef(i, f"bss_{i}", 0, 2, is_safety_item=True) for i in range(1, 20)]
        + [ItemDef(20, "bss_prior_attempts", 0, 2, scored=False, is_safety_item=True),
           ItemDef(21, "bss_last_attempt_intent", 0, 2, scored=False, is_safety_item=True)],
    ),
    # Valor 1 = el participante marcó "Verdadero"; la clave se aplica en el servidor
    InstrumentDef("BHS", "Beck Hopelessness Scale", "1", "bhs", "predictor", "baseline+final",
                  _likert("bhs", 20, 0, 1)),
    InstrumentDef("ULS6", "UCLA Loneliness Scale - versión corta de 6 ítems", "1", "sum", "predictor", "weekly",
                  _likert("uls6", 6, 1, 4)),
    InstrumentDef("UCLA3", "UCLA Loneliness Scale - 3 ítems", "1", "sum", "predictor", "monthly",
                  _likert("ucla", 3, 1, 3)),
    InstrumentDef("ISI", "Insomnia Severity Index", "1", "sum", "predictor", "biweekly",
                  _likert("isi", 7, 0, 4)),
    InstrumentDef("MSPSS", "Multidimensional Scale of Perceived Social Support", "1", "mean", "predictor", "quarterly",
                  _likert("mspss", 12, 1, 7)),
    InstrumentDef("ADVERSITY", "Adversidades recientes (violencia, abuso, discriminación, bullying)", "1", "sum",
                  "predictor", "once",
                  [ItemDef(1, "violence", 0, 1), ItemDef(2, "abuse", 0, 1),
                   ItemDef(3, "discrimination", 0, 1), ItemDef(4, "bullying", 0, 1)]),
    InstrumentDef("GAD7", "Generalized Anxiety Disorder-7", "1", "sum", "predictor", "biweekly",
                  _likert("gad7", 7, 0, 3)),
    # EMA diaria por chatbot: sin ítems de ideación (decisión metodológica del artículo)
    InstrumentDef("EMA_DAILY", "EMA diaria (afecto, estrés, soledad, sueño percibido)", "1", "none", "ema", "ema",
                  [ItemDef(1, "affect", 0, 10), ItemDef(2, "stress", 0, 10),
                   ItemDef(3, "loneliness", 0, 10), ItemDef(4, "sleep_quality", 0, 10)]),
    InstrumentDef("SUS", "System Usability Scale", "1", "sus", "usability", "final",
                  _likert("sus", 10, 1, 5)),
]

INSTRUMENTS_BY_CODE = {i.code: i for i in INSTRUMENTS}
METRICS_BY_CODE = {m.code: m for m in METRICS}

# Clave de la BHS: ítems que suman 1 punto cuando se responde "Falso".
BHS_FALSE_KEYED = {1, 3, 5, 6, 8, 10, 13, 15, 19}
