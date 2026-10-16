"""Reglas del protocolo de derivación.

Cada regla genera una ALERTA PARA REVISIÓN HUMANA en el tablero. El sistema no
diagnostica ni contacta al estudiante por sí mismo: un profesional autorizado revisa
cada alerta y decide la derivación. Los puntos de corte los fija el equipo clínico
mediante variables de entorno (ver app/config.py).

Además, `support_resources=True` le indica a la app que muestre de inmediato los
recursos de ayuda (p. ej., línea 113 opción 5 del MINSA) al participante.
"""
from dataclasses import dataclass, field

from app.config import Settings
from app.services.scoring import ScoreResult

SEVERITY_ORDER = {"low": 0, "moderate": 1, "high": 2}


@dataclass
class AlertSpec:
    rule_code: str
    severity: str
    detail: dict = field(default_factory=dict)


@dataclass
class SafetyOutcome:
    alerts: list[AlertSpec]

    @property
    def support_resources(self) -> bool:
        return any(SEVERITY_ORDER[a.severity] >= SEVERITY_ORDER["moderate"] for a in self.alerts)


def evaluate_questionnaire(instrument_code: str, result: ScoreResult, s: Settings) -> SafetyOutcome:
    alerts: list[AlertSpec] = []
    v = result.values

    if instrument_code == "PHQ9" and v[9] >= s.phq9_item9_alert_min:
        alerts.append(AlertSpec(
            "phq9_item9", "high" if v[9] >= 2 else "moderate",
            {"item9": v[9], "action": "aplicar C-SSRS y revisión profesional"},
        ))

    elif instrument_code == "BSS" and result.total is not None and result.total >= s.bss_alert_cutoff:
        alerts.append(AlertSpec(
            "bss_positive", "high",
            {"total": result.total, "cutoff": s.bss_alert_cutoff,
             "action": "activar protocolo de derivación (independiente del modelo)"},
        ))

    elif instrument_code == "CSSRS_SCREEN":
        level = result.subscores["ideation_level"]
        if result.subscores["behavior_3m"] == 1 or level >= 4:
            sev = "high"
        elif level >= 2 or result.subscores["behavior_lifetime"] == 1:
            sev = "moderate"
        elif level == 1:
            sev = "low"
        else:
            sev = None
        if sev:
            alerts.append(AlertSpec("cssrs_positive", sev, dict(result.subscores)))

    elif instrument_code == "NSSI" and v[1] == 1:
        alerts.append(AlertSpec("nssi_reported", "moderate",
                                {"action": "evaluación humana; registrar separado de intento suicida"}))

    elif instrument_code == "ADVERSITY" and s.flag_adversity_disclosure and (result.total or 0) > 0:
        alerts.append(AlertSpec("adversity_disclosed", "low",
                                {"n_adversities": result.total, "action": "seguimiento con protocolo sensible"}))

    return SafetyOutcome(alerts)


def evaluate_prediction(score: float, threshold: float, predicted_positive: bool) -> list[AlertSpec]:
    if not predicted_positive:
        return []
    return [AlertSpec("model_positive", "moderate",
                      {"score": round(score, 4), "threshold": threshold,
                       "action": "revisión profesional; la predicción no es un diagnóstico"})]
