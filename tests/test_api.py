"""Pruebas de integración de la API contra PostgreSQL real."""
from datetime import UTC, date, datetime, timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import new_id, now_iso


def blocks(metric, source, start, n, value):
    return [{"metric": metric, "source": source,
             "window_start": (start + timedelta(minutes=30 * k)).isoformat(),
             "window_end": (start + timedelta(minutes=30 * (k + 1))).isoformat(), "value": value} for k in range(n)]


def lima_midnight(days_ago: int) -> datetime:
    today = datetime.now(UTC) - timedelta(hours=5)
    return datetime(today.year, today.month, today.day, 5, tzinfo=UTC) - timedelta(days=days_ago)


def phq9(item9: int) -> list[dict]:
    return [{"item": i, "value": 1} for i in range(1, 9)] + [{"item": 9, "value": item9}]


# ------------------------------------------------------------------ auth y permisos
def test_health(app_client):
    assert app_client.get("/health").json()["status"] == "ok"


def test_login_rejects_bad_password(app_client, staff_tokens):
    r = app_client.post("/api/v1/auth/login", json={"email": "admin@test.pe", "password": "incorrecta-123456"})
    assert r.status_code == 401


def test_role_boundaries(app_client, staff_tokens, enrolled):
    _, dev, _ = enrolled
    assert app_client.get("/api/v1/alerts", headers=staff_tokens["researcher"]).status_code == 403
    assert app_client.get("/api/v1/alerts", headers=staff_tokens["clinician"]).status_code == 200
    assert app_client.get("/api/v1/participants", headers=dev).status_code == 401  # token de teléfono
    assert app_client.post("/api/v1/ingest/sensors", headers=staff_tokens["admin"],
                           json={"batch_id": new_id(), "observations": []}).status_code in (401, 422)
    assert app_client.post("/api/v1/participants", headers=staff_tokens["clinician"],
                           json={"consent_version": "v1"}).status_code == 403


# ------------------------------------------------------------------ enrolamiento e identidad
def test_enrollment_identity_is_encrypted_and_audited(app_client, staff_tokens, sql):
    r = app_client.post("/api/v1/participants", headers=staff_tokens["admin"], json={
        "consent_version": "v1", "age_band": "18-20",
        "identity": {"full_name": "María Quispe", "email": "mquispe@unsa.edu.pe", "student_code": "20201234"}})
    assert r.status_code == 201, r.text
    code = r.json()["participant"]["code"]
    assert code.startswith("P") and len(code) == 5
    assert len(r.json()["enrollment_code"]) == 9

    raw = sql("SELECT full_name_enc, student_code_hmac FROM identity.participant_identity i "
              "JOIN core.participants p ON p.id = i.participant_id WHERE p.code = %s", code)[0]
    assert b"Mar" not in bytes(raw[0]) and raw[1] != "20201234"

    dup = app_client.post("/api/v1/participants", headers=staff_tokens["admin"], json={
        "consent_version": "v1", "identity": {"student_code": " 20201234 "}})
    assert dup.status_code == 409

    assert app_client.get(f"/api/v1/participants/{code}/identity",
                          headers=staff_tokens["researcher"]).status_code == 403
    r = app_client.get(f"/api/v1/participants/{code}/identity", headers=staff_tokens["clinician"])
    assert r.json()["full_name"] == "María Quispe"
    assert sql("SELECT count(*) FROM admin.audit_log WHERE action = 'identity.reveal' AND target_id = %s",
               code)[0][0] == 1


def test_minor_age_band_not_allowed(app_client, staff_tokens):
    r = app_client.post("/api/v1/participants", headers=staff_tokens["admin"],
                        json={"consent_version": "v1", "age_band": "15-17"})
    assert r.status_code == 422


def test_device_registration_rules(app_client, staff_tokens, sql):
    body = app_client.post("/api/v1/participants", headers=staff_tokens["admin"],
                           json={"consent_version": "v1"}).json()
    sloppy = body["enrollment_code"].replace("-", "").lower()
    assert app_client.post("/api/v1/devices/register", json={"enrollment_code": sloppy}).status_code == 201
    assert app_client.post("/api/v1/devices/register", json={"enrollment_code": "AAAA-BBBB"}).status_code == 401
    sql("UPDATE core.participants SET enrollment_code_expires_at = now() - interval '1 minute' WHERE code = %s",
        body["participant"]["code"])
    assert app_client.post("/api/v1/devices/register",
                           json={"enrollment_code": body["enrollment_code"]}).status_code == 401


# ------------------------------------------------------------------ ingesta
def test_ingest_accepts_dedupes_and_rejects(app_client, enrolled, sql):
    code, dev, _ = enrolled
    start = lima_midnight(2)
    obs = blocks("steps", "health_connect", start, 4, 120) + blocks("at_home", "phone_gps", start, 2, 1)
    obs += [
        {"metric": "unknown", "source": "x1", "window_start": start.isoformat(),
         "window_end": (start + timedelta(minutes=30)).isoformat(), "value": 1},
        {**blocks("at_home", "phone_gps", start + timedelta(hours=3), 1, 0.5)[0]},
        {**blocks("steps", "health_connect", start + timedelta(minutes=10), 1, 10)[0]},
        {**blocks("steps", "health_connect", datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
                  + timedelta(hours=2), 1, 10)[0]},
        {**blocks("steps", "health_connect", start + timedelta(hours=5), 1, 99999)[0]},
    ]
    batch = {"batch_id": new_id(), "sent_at": now_iso(), "observations": obs}
    r = app_client.post("/api/v1/ingest/sensors", headers=dev, json=batch)
    assert r.status_code == 202, r.text
    res = r.json()
    assert (res["accepted"], res["rejected"], res["duplicate"]) == (6, 5, 0)
    reasons = " | ".join(x["reason"] for x in res["rejections"])
    for frag in ("desconocida", "binario", ":00 o :30", "futuro", "fuera de rango"):
        assert frag in reasons

    again = app_client.post("/api/v1/ingest/sensors", headers=dev, json=batch).json()
    assert again["replayed"] and again["accepted"] == 6

    resend = app_client.post("/api/v1/ingest/sensors", headers=dev,
                             json={"batch_id": new_id(), "observations": obs[:6]}).json()
    assert (resend["accepted"], resend["duplicate"]) == (0, 6)

    assert sql("SELECT status FROM core.participants WHERE code = %s", code)[0][0] == "active"
    assert sql("SELECT count(*) FROM core.sensor_observations o JOIN core.participants p "
               "ON p.id = o.participant_id WHERE p.code = %s", code)[0][0] == 6


def test_batch_id_from_other_device_conflicts(app_client, staff_tokens):
    def make():
        b = app_client.post("/api/v1/participants", headers=staff_tokens["admin"],
                            json={"consent_version": "v1"}).json()
        t = app_client.post("/api/v1/devices/register", json={"enrollment_code": b["enrollment_code"]}).json()
        return {"Authorization": f"Bearer {t['device_token']}"}

    a, b = make(), make()
    batch = {"batch_id": new_id(), "observations": blocks("app_use", "phone_usage", lima_midnight(1), 1, 1)}
    assert app_client.post("/api/v1/ingest/sensors", headers=a, json=batch).status_code == 202
    assert app_client.post("/api/v1/ingest/sensors", headers=b, json=batch).status_code == 409


def test_person_week_view_aggregates(app_client, enrolled, sql):
    code, dev, _ = enrolled
    for d in (3, 2):
        app_client.post("/api/v1/ingest/sensors", headers=dev, json={
            "batch_id": new_id(), "observations": blocks("steps", "health_connect", lima_midnight(d), 48, 100)})
    rows = sql("SELECT day, value, n_windows FROM analytics.v_daily_sensor "
               "WHERE participant_code = %s AND metric = 'steps' ORDER BY day", code)
    assert [(r[1], r[2]) for r in rows] == [(4800, 48), (4800, 48)]
    wk = sql("SELECT sum(n_days), max(mean) FROM analytics.v_person_week_sensor "
             "WHERE participant_code = %s AND metric = 'steps'", code)[0]
    assert wk[0] == 2 and wk[1] == 4800


# ------------------------------------------------------------------ cuestionarios y alertas
def test_phq9_positive_creates_alert_but_device_sees_no_score(app_client, enrolled, staff_tokens):
    code, dev, _ = enrolled
    body = {"client_response_id": new_id(), "instrument": "PHQ9", "context": "baseline",
            "completed_at": now_iso(), "answers": phq9(2)}
    r = app_client.post("/api/v1/questionnaires/responses", headers=dev, json=body)
    assert r.status_code == 201, r.text
    assert r.json()["support_resources"] is True
    assert "total_score" not in r.json()

    replay = app_client.post("/api/v1/questionnaires/responses", headers=dev, json=body).json()
    assert replay["replayed"] and replay["support_resources"]

    alerts = [a for a in app_client.get("/api/v1/alerts", headers=staff_tokens["clinician"]).json()
              if a["participant_code"] == code]
    assert len(alerts) == 1 and alerts[0]["rule_code"] == "phq9_item9" and alerts[0]["severity"] == "high"

    aid = alerts[0]["id"]
    assert app_client.post(f"/api/v1/alerts/{aid}/acknowledge",
                           headers=staff_tokens["clinician"]).json()["status"] == "acknowledged"
    r = app_client.post(f"/api/v1/alerts/{aid}/resolve", headers=staff_tokens["clinician"],
                        json={"status": "referred", "note": "Derivado a servicio psicopedagógico"})
    assert r.json()["status"] == "referred"
    assert app_client.post(f"/api/v1/alerts/{aid}/resolve", headers=staff_tokens["clinician"],
                           json={"status": "closed", "note": "otra vez"}).status_code == 409

    scored = app_client.get(f"/api/v1/participants/{code}/responses", headers=staff_tokens["researcher"]).json()
    assert scored[0]["total_score"] == 10


def test_questionnaire_validation_errors(app_client, enrolled):
    _, dev, _ = enrolled
    base = {"instrument": "PHQ9", "context": "baseline", "completed_at": now_iso()}
    bad = [
        {**base, "client_response_id": new_id(), "answers": phq9(0)[:-1]},
        {**base, "client_response_id": new_id(), "answers": phq9(5)},
        {**base, "client_response_id": new_id(), "instrument": "NOPE", "answers": phq9(0)},
        {**base, "client_response_id": new_id(), "context": "ema", "answers": phq9(0)},
        {**base, "client_response_id": new_id(), "answers": phq9(0) + [{"item": 1, "value": 0}]},
        {**base, "client_response_id": new_id(), "answers": phq9(0),
         "completed_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()},
    ]
    for b in bad:
        assert app_client.post("/api/v1/questionnaires/responses", headers=dev, json=b).status_code == 422, b


def test_ema_schedule_answer_and_adherence(app_client, enrolled, staff_tokens, sql):
    code, dev, _ = enrolled
    today = (datetime.now(UTC) - timedelta(hours=5)).date()
    r = app_client.post(f"/api/v1/participants/{code}/ema-schedule", headers=staff_tokens["researcher"], json={
        "start_date": str(today - timedelta(days=1)), "days": 3, "prompts_per_day": 4, "seed": 42})
    assert r.status_code == 201 and len(r.json()) == 12
    times = sorted(datetime.fromisoformat(p["scheduled_at"]) for p in r.json())
    assert all(9 <= (t.astimezone(UTC) - timedelta(hours=5)).hour < 21 for t in times)

    # Forzamos un aviso vigente para responderlo ahora
    pid = r.json()[0]["id"]
    sql("UPDATE core.ema_prompts SET scheduled_at = now() - interval '10 min', "
        "expires_at = now() + interval '50 min' WHERE id = %s", pid)
    upcoming = app_client.get("/api/v1/ema/prompts", headers=dev).json()
    assert pid in [p["id"] for p in upcoming]
    assert app_client.post(f"/api/v1/ema/prompts/{pid}/delivered", headers=dev).json()["status"] == "delivered"

    ema = {"client_response_id": new_id(), "instrument": "EMA_DAILY", "context": "ema", "ema_prompt_id": pid,
           "completed_at": now_iso(),
           "answers": [{"item": 1, "value": 6}, {"item": 2, "value": 4}, {"item": 3, "value": 2},
                       {"item": 4, "value": 7}]}
    r = app_client.post("/api/v1/questionnaires/responses", headers=dev, json=ema)
    assert r.status_code == 201 and r.json()["support_resources"] is False
    again = {**ema, "client_response_id": new_id()}
    assert app_client.post("/api/v1/questionnaires/responses", headers=dev, json=again).status_code == 409

    adh = {a["participant_code"]: a for a in
           app_client.get("/api/v1/metrics/adherence", headers=staff_tokens["researcher"]).json()}[code]
    assert adh["n_answered_on_time"] == 1 and adh["n_due"] >= 1

    week = sql("SELECT item_code, mean FROM analytics.v_person_week_ema WHERE participant_code = %s "
               "ORDER BY item_code", code)
    assert dict(week) == {"affect": 6, "loneliness": 2, "sleep_quality": 7, "stress": 4}


def test_predictions_create_alert_once(app_client, enrolled, staff_tokens):
    code, _, _ = enrolled
    monday = date.today() - timedelta(days=date.today().weekday())
    p = {"participant_code": code, "week_start": str(monday), "model_name": "enet_multimodal",
         "model_version": "0.1", "score": 0.81, "threshold": 0.4}
    r = app_client.post("/api/v1/predictions", headers=staff_tokens["researcher"], json={"predictions": [p]})
    assert r.json() == {"stored": 1, "updated": 0, "alerts_created": 1, "clinical_bypass_applied": 0}
    r = app_client.post("/api/v1/predictions", headers=staff_tokens["researcher"],
                        json={"predictions": [{**p, "score": 0.9}]})
    assert r.json() == {"stored": 0, "updated": 1, "alerts_created": 0, "clinical_bypass_applied": 0}
    # Una semana que se recalcula de negativa a positiva también debe alertar
    prev_week = str(monday - timedelta(days=7))
    neg = {**p, "week_start": prev_week, "score": 0.1}
    assert app_client.post("/api/v1/predictions", headers=staff_tokens["researcher"],
                           json={"predictions": [neg]}).json()["alerts_created"] == 0
    r = app_client.post("/api/v1/predictions", headers=staff_tokens["researcher"],
                        json={"predictions": [{**neg, "score": 0.7}]})
    assert r.json() == {"stored": 0, "updated": 1, "alerts_created": 1, "clinical_bypass_applied": 0}
    bad = {**p, "week_start": str(monday + timedelta(days=2))}
    assert app_client.post("/api/v1/predictions", headers=staff_tokens["researcher"],
                           json={"predictions": [bad]}).status_code == 422


def test_latency_metrics_available(app_client, enrolled, staff_tokens):
    _, dev, _ = enrolled
    app_client.post("/api/v1/ingest/sensors", headers=dev,
                    json={"batch_id": new_id(), "observations": blocks("app_use", "phone_usage", lima_midnight(1), 2, 1)})
    stats = {s["kind"]: s for s in app_client.get("/api/v1/metrics/latency", headers=staff_tokens["admin"]).json()}
    assert stats["sensor_batch"]["n"] >= 1 and stats["sensor_batch"]["p95_ms"] is not None


# ------------------------------------------------------------------ WebSockets
def test_ws_device_requires_auth(app_client):
    with app_client.websocket_connect("/ws/device") as ws:
        ws.send_json({"type": "auth", "token": "dev_invalido"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_ws_device_ingest_and_dashboard_push(app_client, enrolled, staff_tokens):
    code, dev, _ = enrolled
    token = dev["Authorization"].split()[1]
    jwt_clin = staff_tokens["clinician"]["Authorization"].split()[1]

    with app_client.websocket_connect("/ws/dashboard") as dash:
        dash.send_json({"type": "auth", "token": jwt_clin})
        assert dash.receive_json()["type"] == "auth_ok"

        with app_client.websocket_connect("/ws/device") as ws:
            ws.send_json({"type": "auth", "token": token})
            assert ws.receive_json() == {"type": "auth_ok", "participant_code": code}
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"

            bid = new_id()
            ws.send_json({"type": "sensor_batch", "data": {
                "batch_id": bid, "observations": blocks("steps", "fitbit", lima_midnight(1), 3, 50)}})
            ack = ws.receive_json()
            assert ack["type"] == "ack" and ack["ref"] == bid and ack["data"]["accepted"] == 3

            ws.send_json({"type": "sensor_batch", "data": {"batch_id": "no-uuid"}})
            assert ws.receive_json()["type"] == "error"
            ws.send_json({"type": "sensor_batch", "data": ["no", "es", "objeto"]})
            assert ws.receive_json()["type"] == "error"  # el canal sigue abierto

            ws.send_json({"type": "questionnaire", "data": {
                "client_response_id": new_id(), "instrument": "NSSI", "context": "weekly",
                "completed_at": now_iso(), "answers": [{"item": 1, "value": 1}]}})
            assert ws.receive_json()["data"]["support_resources"] is True

        events = [dash.receive_json() for _ in range(2)]
        assert events[0]["type"] == "ingest.batch" and events[0]["data"]["participant_code"] == code
        assert events[1]["type"] == "alert.created" and events[1]["data"]["rule_code"] == "nssi_reported"


def test_ws_dashboard_rejects_researcher(app_client, staff_tokens):
    with app_client.websocket_connect("/ws/dashboard") as dash:
        dash.send_json({"type": "auth", "token": staff_tokens["researcher"]["Authorization"].split()[1]})
        with pytest.raises(WebSocketDisconnect):
            dash.receive_json()


# ------------------------------------------------------------------ retiro
def test_withdraw_revokes_access_and_purges_identity(app_client, staff_tokens, sql):
    b = app_client.post("/api/v1/participants", headers=staff_tokens["admin"], json={
        "consent_version": "v1", "identity": {"full_name": "Retirado Test"}}).json()
    code = b["participant"]["code"]
    tok = app_client.post("/api/v1/devices/register", json={"enrollment_code": b["enrollment_code"]}).json()
    dev = {"Authorization": f"Bearer {tok['device_token']}"}
    app_client.post("/api/v1/ingest/sensors", headers=dev, json={
        "batch_id": new_id(), "observations": blocks("steps", "health_connect", lima_midnight(1), 2, 10)})

    assert app_client.post(f"/api/v1/participants/{code}/withdraw", headers=staff_tokens["researcher"],
                           json={}).status_code == 403
    assert app_client.post(f"/api/v1/participants/{code}/withdraw", headers=staff_tokens["admin"],
                           json={"purge_identity": True}).status_code == 204
    assert app_client.post("/api/v1/ingest/sensors", headers=dev, json={
        "batch_id": new_id(), "observations": blocks("steps", "health_connect", lima_midnight(1), 1, 1)}
    ).status_code == 401
    assert sql("SELECT count(*) FROM identity.participant_identity i JOIN core.participants p "
               "ON p.id = i.participant_id WHERE p.code = %s", code)[0][0] == 0
    assert sql("SELECT count(*) FROM analytics.v_daily_sensor WHERE participant_code = %s", code)[0][0] == 0

    assert app_client.post(f"/api/v1/participants/{code}/withdraw", headers=staff_tokens["admin"],
                           json={"purge_data": True}).status_code == 204
    assert sql("SELECT count(*) FROM core.participants WHERE code = %s", code)[0][0] == 0


# ------------------------------------------------------------------ alineación con el artículo
def test_minor_requires_assent_and_guardian_consent(app_client, staff_tokens, sql):
    base = {"consent_version": "v1", "age_band": "16-17"}
    assert app_client.post("/api/v1/participants", headers=staff_tokens["admin"], json=base).status_code == 422
    ok = app_client.post("/api/v1/participants", headers=staff_tokens["admin"], json={
        **base, "assent_at": now_iso(), "guardian_consent_at": now_iso()})
    assert ok.status_code == 201, ok.text
    assert ok.json()["participant"]["guardian_consent_at"] is not None
    with pytest.raises(Exception, match="minor_consent"):  # la BD también lo exige
        sql("UPDATE core.participants SET guardian_consent_at = NULL WHERE code = %s",
            ok.json()["participant"]["code"])


def _answers(n, v):
    return [{"item": i, "value": v} for i in range(1, n + 1)]


def test_features_view_and_server_side_bypass(app_client, enrolled, staff_tokens):
    code, dev, _ = enrolled
    d = lima_midnight(1)
    obs = (blocks("steps", "health_connect", d, 48, 100) + blocks("at_home", "phone_gps", d, 20, 1)
           + blocks("app_use", "phone_usage", d, 10, 1)
           + [{"metric": "sleep_minutes", "source": "fitbit", "window_start": (d - timedelta(hours=2)).isoformat(),
               "window_end": (d + timedelta(hours=6)).isoformat(), "value": 420}])
    assert app_client.post("/api/v1/ingest/sensors", headers=dev,
                           json={"batch_id": new_id(), "observations": obs}).json()["rejected"] == 0
    when = (d + timedelta(hours=10)).isoformat()
    for ins, ans in [("PHQ9", phq9(1)), ("GAD7", _answers(7, 2)), ("ULS6", _answers(6, 2)),
                     ("CSSRS_SCREEN", [{"item": 1, "value": 0}, {"item": 2, "value": 0}, {"item": 6, "value": 0}])]:
        r = app_client.post("/api/v1/questionnaires/responses", headers=dev, json={
            "client_response_id": new_id(), "instrument": ins, "context": "weekly", "completed_at": when,
            "answers": ans})
        assert r.status_code == 201, (ins, r.text)

    # El rol de servicio (microservicio ML) lee features; un clínico no
    assert app_client.get("/api/v1/features/person-week", headers=staff_tokens["clinician"]).status_code == 403
    rows = [f for f in app_client.get("/api/v1/features/person-week", headers=staff_tokens["service"]).json()
            if f["participant_code"] == code]
    assert len(rows) == 1
    f = rows[0]
    assert f["steps_daily_mean"] == 4800 and f["home_minutes_mean"] == 600 and f["app_use_minutes_mean"] == 300
    assert f["sleep_minutes_mean"] == 420
    assert (f["phq9_total"], f["phq9_total_excl_item9"], f["gad7_total"], f["uls6_total"], f["cssrs_level"]) == \
           (9, 8, 14, 12, 0)
    assert f["label_high_risk"] is True  # ítem 9 = 1

    # Probabilidad baja, pero el gatillo clínico fuerza "alto" y no duplica la alerta
    r = app_client.post("/api/v1/predictions", headers=staff_tokens["service"], json={"predictions": [{
        "participant_code": code, "week_start": f["week_start"], "model_name": "random_forest",
        "model_version": "1", "score": 0.05}]})
    assert r.json() == {"stored": 1, "updated": 0, "alerts_created": 0, "clinical_bypass_applied": 1}
    assert app_client.get("/api/v1/alerts", headers=staff_tokens["service"]).status_code == 403
