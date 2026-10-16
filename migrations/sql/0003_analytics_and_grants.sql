-- =====================================================================
-- Vistas analíticas (unidad de análisis: persona-día y persona-semana)
-- Solo exponen el código seudónimo; nunca datos de identity.
-- La semana empieza el lunes, en hora de Lima.
-- =====================================================================

CREATE VIEW analytics.v_daily_sensor AS
SELECT
    p.code   AS participant_code,
    o.metric,
    o.source,
    CASE WHEN m.granularity = 'night'
         THEN (o.window_end   AT TIME ZONE 'America/Lima')::date   -- la noche cuenta para el día en que despierta
         ELSE (o.window_start AT TIME ZONE 'America/Lima')::date
    END      AS day,
    CASE m.daily_agg
         WHEN 'sum'  THEN sum(o.value)
         WHEN 'mean' THEN avg(o.value)
         ELSE max(o.value)
    END      AS value,
    count(*) AS n_windows       -- cobertura: p. ej., 48 bloques de 30 min = día completo
FROM core.sensor_observations o
JOIN core.participants p       ON p.id = o.participant_id
JOIN core.metric_definitions m ON m.code = o.metric
WHERE p.status <> 'withdrawn'
GROUP BY p.code, o.metric, o.source, m.granularity, m.daily_agg, 4;

CREATE VIEW analytics.v_person_week_sensor AS
SELECT
    participant_code,
    metric,
    source,
    date_trunc('week', day)::date AS week_start,
    avg(value)          AS mean,
    stddev_samp(value)  AS sd,
    min(value)          AS min,
    max(value)          AS max,
    count(*)            AS n_days,
    sum(n_windows)      AS n_windows
FROM analytics.v_daily_sensor
GROUP BY participant_code, metric, source, date_trunc('week', day);

CREATE VIEW analytics.v_person_week_ema AS
SELECT
    p.code AS participant_code,
    date_trunc('week', (r.completed_at AT TIME ZONE 'America/Lima'))::date AS week_start,
    r.instrument_code,
    ii.code             AS item_code,
    avg(ir.value)       AS mean,
    stddev_samp(ir.value) AS sd_within,   -- variabilidad intraindividual
    count(*)            AS n_responses
FROM core.questionnaire_responses r
JOIN core.instruments i       ON i.code = r.instrument_code AND i.purpose = 'ema'
JOIN core.item_responses ir   ON ir.response_id = r.id
JOIN core.instrument_items ii ON ii.instrument_code = r.instrument_code AND ii.item_no = ir.item_no
JOIN core.participants p      ON p.id = r.participant_id
WHERE p.status <> 'withdrawn'
GROUP BY p.code, 2, r.instrument_code, ii.code;

-- Último puntaje de cada instrumento (no EMA) por persona-semana.
-- Aquí sale la etiqueta semanal (BSS) y los cuestionarios basales.
CREATE VIEW analytics.v_person_week_questionnaires AS
SELECT DISTINCT ON (p.code, r.instrument_code, 3)
    p.code AS participant_code,
    r.instrument_code,
    date_trunc('week', (r.completed_at AT TIME ZONE 'America/Lima'))::date AS week_start,
    r.context,
    r.total_score,
    r.subscores,
    r.completed_at
FROM core.questionnaire_responses r
JOIN core.instruments i  ON i.code = r.instrument_code AND i.purpose <> 'ema'
JOIN core.participants p ON p.id = r.participant_id
WHERE p.status <> 'withdrawn'
ORDER BY p.code, r.instrument_code, 3, r.completed_at DESC;

-- Adherencia a EMA (H5): respondidas a tiempo / programadas ya vencidas
CREATE VIEW analytics.v_ema_adherence AS
WITH base AS (
    SELECT
        p.code AS participant_code,
        count(*) FILTER (WHERE e.status <> 'cancelled'
                           AND (e.expires_at <= now() OR e.status IN ('answered', 'answered_late'))) AS n_due,
        count(*) FILTER (WHERE e.status = 'answered')      AS n_answered_on_time,
        count(*) FILTER (WHERE e.status = 'answered_late') AS n_answered_late
    FROM core.ema_prompts e
    JOIN core.participants p ON p.id = e.participant_id
    GROUP BY p.code
)
SELECT *,
       CASE WHEN n_due > 0 THEN n_answered_on_time::double precision / n_due END AS adherence
FROM base;

-- Latencia de procesamiento (H4): recepción -> resultado
CREATE VIEW analytics.v_processing_latency AS
SELECT 'sensor_batch'::text AS kind, b.id AS ref_id, b.received_at,
       extract(epoch FROM (b.processed_at - b.received_at)) * 1000 AS latency_ms
FROM core.ingest_batches b
WHERE b.processed_at IS NOT NULL
UNION ALL
SELECT 'questionnaire'::text, r.id, r.received_at,
       extract(epoch FROM (r.processed_at - r.received_at)) * 1000
FROM core.questionnaire_responses r
WHERE r.processed_at IS NOT NULL;

-- =====================================================================
-- Permisos por rol (los roles se crean en db/init/01_roles.sh)
--   gsti_api      la API: core + identity + admin
--   gsti_analyst  equipo de modelado: SOLO vistas analytics
-- =====================================================================
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gsti_api') THEN
        GRANT USAGE ON SCHEMA core, identity, admin, analytics TO gsti_api;
        GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA core, identity, admin TO gsti_api;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA core, admin TO gsti_api;
        GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO gsti_api;
        -- La bitácora de auditoría es de solo-anexar para la API
        REVOKE UPDATE, DELETE ON admin.audit_log FROM gsti_api;
        -- El catálogo de métricas e instrumentos solo cambia por migración
        REVOKE INSERT, UPDATE, DELETE ON core.metric_definitions, core.instruments, core.instrument_items FROM gsti_api;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'gsti_analyst') THEN
        GRANT USAGE ON SCHEMA analytics TO gsti_analyst;
        GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO gsti_analyst;
    END IF;
END
$$;
