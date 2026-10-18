-- =====================================================================
-- 0004 · Alineación con el artículo (secciones 3.3, 3.4, 3.10, 3.11, 4.6)
-- =====================================================================

-- 3.3/3.4 · Población de 16 a 35 años. Los menores (16-17) requieren asentimiento
-- del participante y autorización del representante legal antes de enrolarse.
ALTER TABLE core.participants DROP CONSTRAINT IF EXISTS participants_age_band_check;
ALTER TABLE core.participants
    ADD CONSTRAINT participants_age_band_check
        CHECK (age_band IN ('16-17', '18-20', '21-23', '24-26', '27-29', '30-35')),
    ADD COLUMN assent_at            timestamptz,
    ADD COLUMN guardian_consent_at  timestamptz,
    ADD CONSTRAINT participants_minor_consent_check
        CHECK (age_band IS DISTINCT FROM '16-17'
               OR (assent_at IS NOT NULL AND guardian_consent_at IS NOT NULL));

-- 4.6 · Cuenta de servicio para el microservicio de inferencia (RF / XGBoost)
ALTER TABLE admin.staff_users DROP CONSTRAINT IF EXISTS staff_users_role_check;
ALTER TABLE admin.staff_users
    ADD CONSTRAINT staff_users_role_check CHECK (role IN ('admin', 'clinician', 'researcher', 'service'));

-- 4.6.1 · Niveles de riesgo (bajo/moderado/alto), umbral 0.35 y gatillo clínico
ALTER TABLE core.model_predictions
    ALTER COLUMN threshold SET DEFAULT 0.35,
    ADD COLUMN risk_level       text NOT NULL DEFAULT 'bajo' CHECK (risk_level IN ('bajo', 'moderado', 'alto')),
    ADD COLUMN clinical_bypass  boolean NOT NULL DEFAULT false;
ALTER TABLE core.model_predictions ALTER COLUMN risk_level DROP DEFAULT;

-- ---------------------------------------------------------------------
-- 3.10 / 3.11 · Vector de 8 indicadores por persona-semana + etiqueta de referencia
--   Pasivos (wearable/smartphone): pasos diarios, minutos de sueño,
--     minutos en casa (bloques x 30), minutos con uso de apps (bloques x 30)
--   Autoinforme (chatbot): PHQ-9, GAD-7, ULS-6, nivel C-SSRS
--   Etiqueta: C-SSRS >= 3 o PHQ-9 ítem 9 > 0 (mismo criterio del gatillo clínico)
-- Los valores faltantes quedan en NULL: la imputación (k-NN / IterativeImputer)
-- se hace dentro del pipeline del modelo, nunca en la base de datos.
-- ---------------------------------------------------------------------
CREATE VIEW analytics.v_person_week_features AS
WITH sensor AS (
    SELECT participant_code, date_trunc('week', day)::date AS week_start,
           avg(value) FILTER (WHERE metric = 'steps')              AS steps_daily_mean,
           avg(value) FILTER (WHERE metric = 'sleep_minutes')      AS sleep_minutes_mean,
           avg(value * 30) FILTER (WHERE metric = 'at_home')       AS home_minutes_mean,
           avg(value * 30) FILTER (WHERE metric = 'app_use')       AS app_use_minutes_mean,
           count(DISTINCT day)                                     AS n_days_sensed
    FROM analytics.v_daily_sensor
    WHERE metric IN ('steps', 'sleep_minutes', 'at_home', 'app_use')
    GROUP BY participant_code, date_trunc('week', day)
),
q AS (   -- último puntaje de cada instrumento en la semana
    SELECT DISTINCT ON (p.code, r.instrument_code, 3)
           p.code AS participant_code, r.instrument_code,
           date_trunc('week', (r.completed_at AT TIME ZONE 'America/Lima'))::date AS week_start,
           r.id AS response_id, r.total_score
    FROM core.questionnaire_responses r
    JOIN core.participants p ON p.id = r.participant_id
    WHERE p.status <> 'withdrawn'
      AND r.instrument_code IN ('PHQ9', 'GAD7', 'ULS6', 'CSSRS_SCREEN')
    ORDER BY p.code, r.instrument_code, 3, r.completed_at DESC
),
qw AS (
    SELECT q.participant_code, q.week_start,
           max(q.total_score) FILTER (WHERE q.instrument_code = 'PHQ9')         AS phq9_total,
           max(i9.value)      FILTER (WHERE q.instrument_code = 'PHQ9')         AS phq9_item9,
           max(q.total_score) FILTER (WHERE q.instrument_code = 'GAD7')         AS gad7_total,
           max(q.total_score) FILTER (WHERE q.instrument_code = 'ULS6')         AS uls6_total,
           max(q.total_score) FILTER (WHERE q.instrument_code = 'CSSRS_SCREEN') AS cssrs_level
    FROM q
    LEFT JOIN core.item_responses i9 ON i9.response_id = q.response_id AND q.instrument_code = 'PHQ9'
                                    AND i9.item_no = 9
    GROUP BY q.participant_code, q.week_start
)
SELECT
    coalesce(s.participant_code, qw.participant_code) AS participant_code,
    coalesce(s.week_start, qw.week_start)             AS week_start,
    s.steps_daily_mean, s.sleep_minutes_mean, s.home_minutes_mean, s.app_use_minutes_mean,
    qw.phq9_total,
    qw.phq9_total - qw.phq9_item9                     AS phq9_total_excl_item9,
    qw.gad7_total, qw.uls6_total, qw.cssrs_level,
    qw.phq9_item9,
    coalesce(s.n_days_sensed, 0)                      AS n_days_sensed,
    CASE WHEN qw.cssrs_level IS NULL AND qw.phq9_item9 IS NULL THEN NULL
         ELSE (coalesce(qw.cssrs_level, 0) >= 3 OR coalesce(qw.phq9_item9, 0) > 0)
    END                                               AS label_high_risk
FROM sensor s
FULL OUTER JOIN qw ON qw.participant_code = s.participant_code AND qw.week_start = s.week_start;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gsti_api') THEN
        GRANT SELECT ON analytics.v_person_week_features TO gsti_api;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gsti_analyst') THEN
        GRANT SELECT ON analytics.v_person_week_features TO gsti_analyst;
    END IF;
END
$$;
