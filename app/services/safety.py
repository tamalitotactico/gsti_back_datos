"""Reglas del protocolo de derivación y gatillo clínico directo (artículo, secciones 3.10 y 4.6.2).

Gatillo clínico directo (clinical bypass): si el ítem 9 del PHQ-9 es > 0 o el nivel de
ideación de la C-SSRS es >= 3, el sistema asigna "Riesgo alto" sin esperar al modelo
predictivo y emite de inmediato una alerta en el tablero psicopedagógico.

Cada alerta es PARA REVISIÓN HUMANA: un profesional autorizado la revisa y decide la
derivación; el sistema no diagnostica. `support_resources=True` indica a la app que muestre
de inmediato los recursos de ayuda al participante.

Severidad de la alerta ↔ nivel de riesgo del artículo: low=bajo, moderate=moderado, high=alto.
"""
from dataclasses import dataclass, field

from app.config import Settings
from app.services.scoring import ScoreResult

SEVERITY_ORDER = {"low": 0, "moderate": 1, "high": 2}
RISK_LEVEL = {"low": "bajo", "moderate": "moderado", "high": "alto"}


@dataclass
class AlertSpec:
    rule_code: str
    severity: str
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.detail.setdefault("risk_level", RISK_LEVEL[self.severity])


@dataclass
class SafetyOutcome:
    alerts: list[AlertSpec]

    @property
    def support_resources(self) -> bool:
        return any(SEVERITY_ORDER[a.severity] >= SEVERITY_ORDER["moderate"] for a in self.alerts)

    @property
    def clinical_bypass(self) -> bool:
        return any(a.detail.get("clinical_bypass") for a in self.alerts)


def is_bypass(phq9_item9: int | None, cssrs_level: int | None, s: Settings) -> bool:
    return (phq9_item9 is not None and phq9_item9 >= s.phq9_item9_alert_min) or \
           (cssrs_level is not None and cssrs_level >= s.cssrs_bypass_level)


def evaluate_questionnaire(instrument_code: str, result: ScoreResult, s: Settings) -> SafetyOutcome:
    alerts: list[AlertSpec] = []
    v = result.values

    if instrument_code == "PHQ9" and is_bypass(v[9], None, s):
        alerts.append(AlertSpec(
            "phq9_item9", "high",
            {"item9": v[9], "clinical_bypass": True,
             "action": "riesgo alto por gatillo clínico; revisión profesional inmediata"},
        ))

    elif instrument_code == "CSSRS_SCREEN":
        level = result.subscores["ideation_level"]
        if is_bypass(None, level, s) or result.subscores["behavior_3m"] == 1:
            alerts.append(AlertSpec("cssrs_positive", "high", {
                **result.subscores, "clinical_bypass": True,
                "action": "riesgo alto por gatillo clínico; revisión profesional inmediata"}))
        elif level == 2 or result.subscores["behavior_lifetime"] == 1:
            alerts.append(AlertSpec("cssrs_positive", "moderate", dict(result.subscores)))
        elif level == 1:
            alerts.append(AlertSpec("cssrs_positive", "low", dict(result.subscores)))

    # --- Instrumentos complementarios (opcionales) ---
    elif instrument_code == "BSS" and result.total is not None and result.total >= s.bss_alert_cutoff:
        alerts.append(AlertSpec("bss_positive", "high",
                                {"total": result.total, "cutoff": s.bss_alert_cutoff}))

    elif instrument_code == "NSSI" and v[1] == 1:
        alerts.append(AlertSpec("nssi_reported", "moderate",
                                {"action": "evaluación humana; registrar separado de intento suicida"}))

    elif instrument_code == "ADVERSITY" and s.flag_adversity_disclosure and (result.total or 0) > 0:
        alerts.append(AlertSpec("adversity_disclosed", "low",
                                {"n_adversities": result.total, "action": "seguimiento con protocolo sensible"}))

    return SafetyOutcome(alerts)


def risk_from_prediction(score: float, threshold: float, suggested: str | None) -> str:
    """Nivel de riesgo a partir de la probabilidad del clasificador (umbral 0.35 por defecto)."""
    if score >= threshold:
        return "alto"
    return suggested if suggested in ("bajo", "moderado") else "bajo"


def evaluate_prediction(score: float, threshold: float, risk_level: str, model_name: str) -> list[AlertSpec]:
    if risk_level != "alto":
        return []
    return [AlertSpec("model_high_risk", "high",
                      {"score": round(score, 4), "threshold": threshold, "model": model_name,
                       "action": "revisión profesional; la predicción no es un diagnóstico"})]
