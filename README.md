# GSTI · Backend

Servidor del *Sistema Móvil Inteligente de Monitoreo Comportamental para la Detección Temprana de Riesgo Suicida en Estudiantes de Sistemas de la UNSA*.

Recibe en JSON (REST y WebSocket) las bioseñales del wearable, los indicadores del smartphone y las respuestas EMA/cuestionarios; los guarda **seudonimizados** en PostgreSQL; puntúa los instrumentos; y genera **alertas para revisión humana** en el tablero. El sistema no diagnostica ni contacta al estudiante por sí mismo.

**Stack:** Python 3.12 · FastAPI · SQLAlchemy 2 (async) · PostgreSQL 16 · Alembic · Docker Compose.

## Arranque rápido (Docker)

```bash
cp .env.example .env          # completar contraseñas y secretos (instrucciones dentro)
docker compose up -d --build  # crea la BD, roles, migra y levanta la API en :8000
docker compose exec api python -m scripts.create_admin --email tu@unsa.edu.pe --name "Tu Nombre"
```

- API y Swagger: http://localhost:8000/docs
- Simular un teléfono con datos sintéticos:
  `python -m scripts.simulate_device --email tu@unsa.edu.pe --password '…' --days 3`

## Pruebas

Requieren un PostgreSQL con superusuario (no hace falta que la API esté corriendo):

```bash
pip install -r requirements-dev.txt
TEST_ADMIN_DSN=postgresql://gsti_owner:<pwd>@localhost:5432/postgres pytest
```

Cubren puntuación de instrumentos, reglas de alerta, permisos por rol, idempotencia, rechazo de datos inválidos, cifrado de identidad, EMA y adherencia, WebSockets, retiro del participante y las vistas analíticas.

## Diseño de privacidad

| Capa | Qué contiene | Quién accede |
|---|---|---|
| `core` | Datos de investigación con seudónimo (`P0001`); sin nombres, correos ni coordenadas | API (`gsti_api`) |
| `identity` | Nombre, correo y teléfono **cifrados** (Fernet; clave fuera de la BD) y código de estudiante como HMAC | API; se revela solo a admin/clínico, con registro en auditoría |
| `analytics` | Vistas persona-día y persona-semana listas para el modelo | Equipo de modelado (`gsti_analyst`), que **no** ve `core` ni `identity` |
| `admin` | Personal y bitácora de auditoría (solo anexar) | API |

Minimización: la ubicación "en casa" se calcula en el teléfono y solo llega un 0/1 por bloque; el uso de apps llega como 0/1 sin nombres; la edad se guarda en bandas (16-17 a 30-35). Para participantes de 16-17 años la base de datos exige registrar el asentimiento y la autorización del representante legal. El retiro del consentimiento revoca dispositivos, cancela EMA futuras y borra la identidad; con `purge_data` borra todo.

## Protocolo de alertas (artículo, secciones 3.10 y 4.6)

**Gatillo clínico directo:** si el ítem 9 del PHQ-9 es > 0 o la C-SSRS marca nivel de ideación ≥ 3, el sistema asigna **Riesgo alto** sin esperar al modelo y emite la alerta de inmediato. El servidor vuelve a aplicar esta regla al recibir predicciones (cláusula de guarda), así que una probabilidad baja nunca la anula.

| Regla | Disparador | Nivel |
|---|---|---|
| `phq9_item9` | PHQ-9 ítem 9 > 0 (gatillo clínico) | alto |
| `cssrs_positive` | C-SSRS nivel ≥ 3 o conducta en 3 meses (gatillo) / nivel 2 o conducta en la vida / nivel 1 | alto / moderado / bajo |
| `model_high_risk` | Probabilidad del clasificador ≥ 0.35 (umbral configurable) | alto |
| `bss_positive`, `nssi_reported`, `adversity_disclosed` | Instrumentos complementarios opcionales | alto / moderado / bajo |

Cada alerta pasa por `abierta → reconocida → derivada/cerrada` con nota y responsable. **Ninguna alerta es un diagnóstico.** Los umbrales se configuran en `.env` y deben validarlos el equipo clínico y el comité de ética.

## Integración con el modelo predictivo (RF / XGBoost)

El entrenamiento e inferencia corren en un microservicio Python aparte, con una cuenta de rol `service`:

1. `GET /api/v1/features/person-week` → vector de 8 indicadores por persona-semana (pasos diarios, minutos de sueño, minutos en casa, minutos con uso de apps, PHQ-9, GAD-7, ULS-6, nivel C-SSRS) y la etiqueta `label_high_risk` (C-SSRS ≥ 3 o PHQ-9 ítem 9 > 0). Los faltantes llegan como `null`: la imputación se hace dentro del pipeline del modelo.
2. `POST /api/v1/predictions` → probabilidad de Riesgo alto por persona-semana; el servidor asigna el nivel (bajo/moderado/alto) y aplica el gatillo clínico.

Nota metodológica: `phq9_total` incluye el ítem 9 y `cssrs_level` coincide con la etiqueta. Para evitar circularidad, el equipo de modelado puede usar `phq9_total_excl_item9` y excluir `cssrs_level` de los predictores.

## Mapa de endpoints

- Personal: `/auth/login`, `/participants` (enrolar, resumen, identidad, retiro, calendario EMA), `/alerts`, `/metrics/latency` (H4), `/metrics/adherence` (H5).
- Microservicio de inferencia (rol `service`): `/features/person-week`, `/predictions`.
- Teléfono: `/devices/register`, `/ingest/sensors`, `/questionnaires/responses`, `/ema/prompts`, `/catalog/*`.
- Tiempo real: `/ws/device` (ingesta) y `/ws/dashboard` (alertas en vivo).

Contrato detallado para el equipo de la app: [docs/contrato-api.md](docs/contrato-api.md). Esquema SQL comentado: [migrations/sql/0001_schema.sql](migrations/sql/0001_schema.sql).

## Limitaciones conocidas

- Un solo proceso de uvicorn: el bus del WebSocket es en memoria. Para escalar, migrar a `LISTEN/NOTIFY` o Redis.
- Las vistas analíticas fijan la zona `America/Lima`.
- Desplegar detrás de HTTPS (proxy inverso) antes de usar datos reales.
- Aún no hay límite de intentos en `/auth/login` ni `/devices/register`; agregarlo (proxy o middleware) antes del piloto.
