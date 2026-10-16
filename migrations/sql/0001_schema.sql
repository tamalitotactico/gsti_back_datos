-- =====================================================================
-- GSTI · Esquema inicial de la base de datos seudonimizada
-- Fuente de verdad del modelo de datos (Sección 4.4 del artículo).
--
-- Esquemas:
--   core      datos de investigación SIN identificadores personales
--   identity  bóveda de identidad (cifrada a nivel de aplicación)
--   admin     usuarios del personal y bitácora de auditoría
--   analytics vistas agregadas persona-día / persona-semana para el modelo
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS identity;
CREATE SCHEMA IF NOT EXISTS admin;
CREATE SCHEMA IF NOT EXISTS analytics;

-- ---------------------------------------------------------------------
-- admin
-- ---------------------------------------------------------------------
CREATE TABLE admin.staff_users (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email          text NOT NULL UNIQUE CHECK (email = lower(email)),
    full_name      text NOT NULL,
    role           text NOT NULL CHECK (role IN ('admin', 'clinician', 'researcher')),
    password_hash  text NOT NULL,
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE admin.audit_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL DEFAULT now(),
    staff_id     uuid REFERENCES admin.staff_users (id),
    action       text NOT NULL,
    target_type  text,
    target_id    text,
    detail       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX audit_log_at_idx ON admin.audit_log (at DESC);

-- ---------------------------------------------------------------------
-- core · participantes y dispositivos
-- ---------------------------------------------------------------------
CREATE SEQUENCE core.participant_code_seq START 1;

CREATE TABLE core.participants (
    id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Seudónimo visible en todo el sistema (P0001, P0002, ...)
    code                        text NOT NULL UNIQUE
                                DEFAULT ('P' || lpad(nextval('core.participant_code_seq')::text, 4, '0'))
                                CHECK (code ~ '^P[0-9]{4,}$'),
    status                      text NOT NULL DEFAULT 'enrolled'
                                CHECK (status IN ('enrolled', 'active', 'withdrawn', 'completed')),
    cohort                      text,
    -- Variables sociodemográficas solo en bandas gruesas (minimización).
    -- La banda mínima es 18-20: la población del estudio son adultos.
    age_band                    text CHECK (age_band IN ('18-20', '21-23', '24-26', '27+')),
    sex                         text CHECK (sex IN ('F', 'M', 'X', 'NR')),
    semester                    smallint CHECK (semester BETWEEN 1 AND 14),
    consent_version             text NOT NULL,
    enrolled_at                 timestamptz NOT NULL DEFAULT now(),
    withdrawn_at                timestamptz,
    enrollment_code_hash        text UNIQUE,
    enrollment_code_expires_at  timestamptz
);

CREATE TABLE core.devices (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_id  uuid NOT NULL REFERENCES core.participants (id) ON DELETE CASCADE,
    kind            text NOT NULL CHECK (kind IN ('smartphone', 'wearable', 'gateway')),
    platform        text,
    model           text,
    app_version     text,
    token_hash      text NOT NULL UNIQUE,   -- SHA-256 del token; el token solo se muestra una vez
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz,
    revoked_at      timestamptz
);
CREATE INDEX devices_participant_idx ON core.devices (participant_id);

-- ---------------------------------------------------------------------
-- core · bioseñales e indicadores comportamentales
-- ---------------------------------------------------------------------
CREATE TABLE core.metric_definitions (
    code         text PRIMARY KEY,
    description  text NOT NULL,
    unit         text NOT NULL,
    value_type   text NOT NULL CHECK (value_type IN ('count', 'minutes', 'binary', 'continuous')),
    granularity  text NOT NULL CHECK (granularity IN ('30min', 'night', 'day', 'sample')),
    min_value    double precision NOT NULL,
    max_value    double precision NOT NULL,
    daily_agg    text NOT NULL CHECK (daily_agg IN ('sum', 'mean', 'max')),
    CHECK (max_value > min_value)
);

CREATE TABLE core.ingest_batches (
    id              uuid PRIMARY KEY,           -- generado por el cliente: idempotencia de reintentos
    device_id       uuid NOT NULL REFERENCES core.devices (id) ON DELETE CASCADE,
    participant_id  uuid NOT NULL REFERENCES core.participants (id) ON DELETE CASCADE,
    channel         text NOT NULL CHECK (channel IN ('rest', 'websocket')),
    client_sent_at  timestamptz,
    received_at     timestamptz NOT NULL DEFAULT now(),
    processed_at    timestamptz,
    n_received      integer NOT NULL DEFAULT 0,
    n_accepted      integer NOT NULL DEFAULT 0,
    n_duplicate     integer NOT NULL DEFAULT 0,
    n_rejected      integer NOT NULL DEFAULT 0
);
CREATE INDEX ingest_batches_received_idx ON core.ingest_batches (received_at DESC);

CREATE TABLE core.sensor_observations (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    participant_id  uuid NOT NULL REFERENCES core.participants (id) ON DELETE CASCADE,
    device_id       uuid NOT NULL REFERENCES core.devices (id) ON DELETE CASCADE,
    batch_id        uuid NOT NULL REFERENCES core.ingest_batches (id) ON DELETE CASCADE,
    metric          text NOT NULL REFERENCES core.metric_definitions (code),
    source          text NOT NULL CHECK (source ~ '^[a-z0-9_]{2,40}$'),
    window_start    timestamptz NOT NULL,
    window_end      timestamptz NOT NULL,
    value           double precision NOT NULL,
    quality         text NOT NULL DEFAULT 'ok' CHECK (quality IN ('ok', 'partial', 'device_imputed')),
    received_at     timestamptz NOT NULL DEFAULT now(),
    CHECK (window_end > window_start),
    -- Un mismo bloque no se guarda dos veces aunque el cliente reenvíe
    CONSTRAINT sensor_obs_unique_window UNIQUE (participant_id, metric, source, window_start)
);
CREATE INDEX sensor_obs_participant_metric_time_idx
    ON core.sensor_observations (participant_id, metric, window_start);

-- ---------------------------------------------------------------------
-- core · instrumentos psicométricos, EMA y respuestas
-- (solo se guardan números de ítem y valores; los textos de los ítems
--  viven en la app, sujetos a la licencia de cada instrumento)
-- ---------------------------------------------------------------------
CREATE TABLE core.instruments (
    code            text PRIMARY KEY,
    name            text NOT NULL,
    version         text NOT NULL,
    n_items         smallint NOT NULL CHECK (n_items > 0),
    scoring         text NOT NULL CHECK (scoring IN ('sum', 'mean', 'bhs', 'bss', 'cssrs', 'sus', 'none')),
    purpose         text NOT NULL CHECK (purpose IN ('predictor', 'reference', 'safety', 'usability', 'ema')),
    administration  text NOT NULL
);

CREATE TABLE core.instrument_items (
    instrument_code  text NOT NULL REFERENCES core.instruments (code) ON DELETE CASCADE,
    item_no          smallint NOT NULL CHECK (item_no > 0),
    code             text NOT NULL,
    min_value        smallint NOT NULL,
    max_value        smallint NOT NULL,
    scored           boolean NOT NULL DEFAULT true,
    is_safety_item   boolean NOT NULL DEFAULT false,
    PRIMARY KEY (instrument_code, item_no),
    CHECK (max_value > min_value)
);

CREATE TABLE core.ema_prompts (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_id   uuid NOT NULL REFERENCES core.participants (id) ON DELETE CASCADE,
    instrument_code  text NOT NULL REFERENCES core.instruments (code),
    scheduled_at     timestamptz NOT NULL,
    expires_at       timestamptz NOT NULL,
    delivered_at     timestamptz,
    status           text NOT NULL DEFAULT 'scheduled'
                     CHECK (status IN ('scheduled', 'delivered', 'answered', 'answered_late', 'cancelled')),
    CHECK (expires_at > scheduled_at),
    UNIQUE (participant_id, instrument_code, scheduled_at)
);
CREATE INDEX ema_prompts_participant_time_idx ON core.ema_prompts (participant_id, scheduled_at);

CREATE TABLE core.questionnaire_responses (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_response_id  uuid NOT NULL UNIQUE,     -- idempotencia de reintentos
    participant_id      uuid NOT NULL REFERENCES core.participants (id) ON DELETE CASCADE,
    device_id           uuid REFERENCES core.devices (id) ON DELETE SET NULL,
    instrument_code     text NOT NULL REFERENCES core.instruments (code),
    context             text NOT NULL CHECK (context IN
                        ('baseline', 'weekly', 'biweekly', 'monthly', 'ema', 'followup', 'final', 'adhoc')),
    ema_prompt_id       uuid UNIQUE REFERENCES core.ema_prompts (id) ON DELETE SET NULL,
    started_at          timestamptz,
    completed_at        timestamptz NOT NULL,
    received_at         timestamptz NOT NULL DEFAULT now(),
    processed_at        timestamptz,
    total_score         double precision,
    subscores           jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX q_responses_participant_instr_idx
    ON core.questionnaire_responses (participant_id, instrument_code, completed_at);

CREATE TABLE core.item_responses (
    response_id  uuid NOT NULL REFERENCES core.questionnaire_responses (id) ON DELETE CASCADE,
    item_no      smallint NOT NULL,
    value        smallint NOT NULL,
    PRIMARY KEY (response_id, item_no)
);

-- ---------------------------------------------------------------------
-- core · predicciones del modelo y alertas para revisión humana
-- ---------------------------------------------------------------------
CREATE TABLE core.model_predictions (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_id      uuid NOT NULL REFERENCES core.participants (id) ON DELETE CASCADE,
    week_start          date NOT NULL,
    model_name          text NOT NULL,
    model_version       text NOT NULL,
    score               double precision NOT NULL CHECK (score BETWEEN 0 AND 1),
    threshold           double precision NOT NULL CHECK (threshold BETWEEN 0 AND 1),
    predicted_positive  boolean NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (participant_id, week_start, model_name, model_version)
);

CREATE TABLE core.alerts (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_id   uuid NOT NULL REFERENCES core.participants (id) ON DELETE CASCADE,
    source_type      text NOT NULL CHECK (source_type IN ('questionnaire', 'model', 'manual')),
    response_id      uuid REFERENCES core.questionnaire_responses (id) ON DELETE SET NULL,
    prediction_id    uuid REFERENCES core.model_predictions (id) ON DELETE SET NULL,
    rule_code        text NOT NULL,
    severity         text NOT NULL CHECK (severity IN ('low', 'moderate', 'high')),
    detail           jsonb NOT NULL DEFAULT '{}'::jsonb,
    status           text NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'acknowledged', 'referred', 'closed')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    acknowledged_at  timestamptz,
    acknowledged_by  uuid REFERENCES admin.staff_users (id),
    resolved_at      timestamptz,
    resolved_by      uuid REFERENCES admin.staff_users (id),
    resolution_note  text
);
CREATE INDEX alerts_status_idx ON core.alerts (status, severity, created_at DESC);
CREATE INDEX alerts_participant_idx ON core.alerts (participant_id, created_at DESC);

-- ---------------------------------------------------------------------
-- identity · bóveda separada. Nombre, correo y teléfono se cifran en la
-- aplicación (Fernet); la clave NO está en la base de datos. El código de
-- estudiante se guarda como HMAC para detectar duplicados sin descifrar.
-- ---------------------------------------------------------------------
CREATE TABLE identity.participant_identity (
    participant_id      uuid PRIMARY KEY REFERENCES core.participants (id) ON DELETE CASCADE,
    student_code_hmac   text UNIQUE,
    full_name_enc       bytea,
    email_enc           bytea,
    phone_enc           bytea,
    created_at          timestamptz NOT NULL DEFAULT now()
);
