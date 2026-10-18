"""Pruebas unitarias de puntuación y reglas de seguridad (sin base de datos)."""
import pytest

from app.catalog import INSTRUMENTS_BY_CODE as I
from app.config import Settings
from app.services.safety import evaluate_questionnaire, risk_from_prediction
from app.services.scoring import ScoringError, validate_and_score

S = Settings(bss_alert_cutoff=1, phq9_item9_alert_min=1)


def full(code, value):
    return {it.item_no: value for it in I[code].items}


def test_phq9_sum_and_item9_alert():
    r = validate_and_score(I["PHQ9"], {**full("PHQ9", 1), 9: 2})
    assert r.total == 10
    out = evaluate_questionnaire("PHQ9", r, S)
    assert [(a.rule_code, a.severity) for a in out.alerts] == [("phq9_item9", "high")]
    assert out.support_resources


def test_phq9_item9_one_triggers_clinical_bypass():
    r = validate_and_score(I["PHQ9"], {**full("PHQ9", 0), 9: 1})
    out = evaluate_questionnaire("PHQ9", r, S)
    assert out.alerts[0].severity == "high" and out.clinical_bypass
    assert out.alerts[0].detail["risk_level"] == "alto"


def test_cssrs_level3_is_bypass_level2_moderate():
    r3 = validate_and_score(I["CSSRS_SCREEN"], {1: 1, 2: 1, 3: 1, 4: 0, 5: 0, 6: 0})
    out3 = evaluate_questionnaire("CSSRS_SCREEN", r3, S)
    assert r3.total == 3 and out3.alerts[0].severity == "high" and out3.clinical_bypass
    r2 = validate_and_score(I["CSSRS_SCREEN"], {1: 1, 2: 1, 3: 0, 4: 0, 5: 0, 6: 0})
    out2 = evaluate_questionnaire("CSSRS_SCREEN", r2, S)
    assert out2.alerts[0].severity == "moderate" and not out2.clinical_bypass


def test_uls6_sum():
    assert validate_and_score(I["ULS6"], full("ULS6", 1)).total == 6
    assert validate_and_score(I["ULS6"], full("ULS6", 4)).total == 24
    with pytest.raises(ScoringError):
        validate_and_score(I["ULS6"], {**full("ULS6", 1), 2: 5})


def test_prediction_risk_levels():
    assert risk_from_prediction(0.35, 0.35, None) == "alto"
    assert risk_from_prediction(0.34, 0.35, "moderado") == "moderado"
    assert risk_from_prediction(0.10, 0.35, None) == "bajo"


def test_phq9_no_alert_when_item9_zero():
    r = validate_and_score(I["PHQ9"], {**full("PHQ9", 3), 9: 0})
    assert r.total == 24
    assert evaluate_questionnaire("PHQ9", r, S).alerts == []


def test_missing_item_rejected():
    raw = full("PHQ9", 0)
    del raw[5]
    with pytest.raises(ScoringError, match="Faltan"):
        validate_and_score(I["PHQ9"], raw)


def test_out_of_range_and_unknown_items_rejected():
    with pytest.raises(ScoringError, match="fuera de rango"):
        validate_and_score(I["GAD7"], {**full("GAD7", 0), 3: 4})
    with pytest.raises(ScoringError, match="inexistentes"):
        validate_and_score(I["GAD7"], {**full("GAD7", 0), 8: 1})


def test_bhs_key():
    # Todo "verdadero": puntúan los 11 ítems con clave verdadera
    assert validate_and_score(I["BHS"], full("BHS", 1)).total == 11
    # Todo "falso": puntúan los 9 ítems con clave falsa
    assert validate_and_score(I["BHS"], full("BHS", 0)).total == 9


def test_sus_scoring():
    assert validate_and_score(I["SUS"], full("SUS", 3)).total == 50
    best = {i: (5 if i % 2 else 1) for i in range(1, 11)}
    assert validate_and_score(I["SUS"], best).total == 100


def test_mspss_mean():
    assert validate_and_score(I["MSPSS"], full("MSPSS", 5)).total == 5


def test_cssrs_skip_logic_and_levels():
    r = validate_and_score(I["CSSRS_SCREEN"], {1: 1, 2: 0, 6: 0})  # 3-5 y 7 omitidos
    assert r.subscores == {"ideation_level": 1, "behavior_lifetime": 0, "behavior_3m": 0}
    assert evaluate_questionnaire("CSSRS_SCREEN", r, S).alerts[0].severity == "low"

    r = validate_and_score(I["CSSRS_SCREEN"], {1: 1, 2: 1, 3: 1, 4: 1, 5: 0, 6: 0})
    assert r.total == 4
    assert evaluate_questionnaire("CSSRS_SCREEN", r, S).alerts[0].severity == "high"

    with pytest.raises(ScoringError, match="Faltan"):
        validate_and_score(I["CSSRS_SCREEN"], {1: 1, 2: 1, 6: 0})  # con ítem 2 = sí, 3-5 son obligatorios
    with pytest.raises(ScoringError, match="requiere"):
        validate_and_score(I["CSSRS_SCREEN"], {1: 0, 2: 0, 6: 0, 7: 1})


def test_cssrs_negative_has_no_alert():
    r = validate_and_score(I["CSSRS_SCREEN"], {1: 0, 2: 0, 6: 0})
    assert evaluate_questionnaire("CSSRS_SCREEN", r, S).alerts == []


def test_bss_screening_skip():
    raw = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    r = validate_and_score(I["BSS"], raw)
    assert r.total == 0 and r.subscores["skipped_6_19"]
    assert evaluate_questionnaire("BSS", r, S).alerts == []

    with pytest.raises(ScoringError):
        validate_and_score(I["BSS"], {1: 1, 2: 0, 3: 0, 4: 1, 5: 0})  # 4 > 0: 6-19 obligatorios

    pos = {**{i: 0 for i in range(1, 20)}, 4: 1}
    r = validate_and_score(I["BSS"], pos)
    assert r.total == 1
    a = evaluate_questionnaire("BSS", r, S).alerts
    assert a[0].rule_code == "bss_positive" and a[0].severity == "high"


def test_nssi_and_adversity_flags():
    r = validate_and_score(I["NSSI"], {1: 1})
    assert evaluate_questionnaire("NSSI", r, S).alerts[0].rule_code == "nssi_reported"
    r = validate_and_score(I["ADVERSITY"], {1: 0, 2: 1, 3: 0, 4: 0})
    out = evaluate_questionnaire("ADVERSITY", r, S)
    assert out.alerts[0].severity == "low" and not out.support_resources


def test_ema_returns_item_codes():
    r = validate_and_score(I["EMA_DAILY"], {1: 7, 2: 3, 3: 2, 4: 8})
    assert r.total is None
    assert r.subscores == {"affect": 7, "stress": 3, "loneliness": 2, "sleep_quality": 8}
