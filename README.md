# Compra — Radar de Oportunidades

**Sistema de arbitraje de marketplaces: detecta productos que se pueden comprar barato y
revender con ganancia, automáticamente.**

Compra recolecta listings de **Mercado Libre, eBay y Facebook Marketplace**, resuelve qué
publicaciones corresponden al *mismo producto físico* (aunque tengan títulos distintos), y
calcula el **ROI y la ganancia neta real** de cada oportunidad de reventa. El resultado es
un dashboard de oportunidades filtrable, ordenado por rentabilidad.

> Arquitectura de microservicios orientada a eventos. El reto central no es scrapear —
> es el **matching de productos** entre marketplaces con datos sucios y a escala.

---

## Arquitectura

```
Frontend (Angular)
      │  Dashboard + filtros
      ▼
   API (FastAPI)  ──────┬──────────────┐
      │            Auth (JWT/OAuth2)   Billing (Stripe)
      ▼
Opportunity Engine     → ROI + cálculo de ganancia
      ▼
Product Resolution     → matching de productos (el core real)
      ▼
Normalization          → limpieza y tokenización de títulos
      ▼
Scraper Workers        → pool de Playwright / Scrapy / Puppeteer
      ▼
Proxy Manager          → rotación de IPs, anti-bloqueo
      ▼
Marketplaces           → Mercado Libre · eBay · Facebook Marketplace
```

Los scrapers y el procesamiento están **desacoplados por una cola de mensajería**
(Redis Streams / RabbitMQ), de modo que la recolección nunca bloquea el análisis.

## Componentes

| Directorio | Rol |
|------------|-----|
| `api/` | API FastAPI: autenticación, endpoints del dashboard |
| `scraper/`, `scraper_v2/` | Workers de scraping (Playwright pool) + gestión de proxies |
| `processor/` | Workers que consumen la cola y procesan listings crudos |
| `engines/` | Opportunity Engine — cálculo de ROI y rentabilidad |
| `truth_engine/` | Product Resolution — matching e identidad de producto entre fuentes |
| `execution_realism/` | Modelado de costos y fricción reales de la operación de reventa |
| `capital_management/` | Gestión de capital / dimensionamiento de las oportunidades |
| `monetization/` | Modelo de cobro (suscripción / billing) |
| `metrics/`, `observability/` | Métricas y observabilidad del sistema |
| `migrations/` | Migraciones de base de datos (PostgreSQL / PL/pgSQL) |
| `infra/` | Infraestructura y despliegue |
| `frontend/` | Dashboard en Angular |

## El core: resolución de productos

El valor está en decidir que estas tres publicaciones son el **mismo** producto:

```
"Sony WH-1000XM4"   "Sony WH1000XM4"   "Sony XM4 headphones"   →   sony wh1000xm4
```

Sistema híbrido de matching en dos niveles:

1. **Identificador exacto** — UPC / EAN / MPN. Si existe, match inmediato.
2. **Fingerprint del producto** — firma `marca + modelo + categoría` tras
   **normalización** (minúsculas, quita símbolos y stop-words, tokeniza), para agrupar
   publicaciones sin identificador estándar.

Solo cuando dos listings apuntan al mismo producto tiene sentido comparar precios y
calcular la oportunidad de arbitraje.

## Stack

- **Backend:** Python · FastAPI · PostgreSQL / PL/pgSQL
- **Scraping:** Playwright · Scrapy · Puppeteer, con pool de workers y rotación de proxies
- **Mensajería:** Redis Streams / RabbitMQ (desacople scraping ↔ procesamiento)
- **Frontend:** Angular (dashboard + filtros)
- **Pagos:** Stripe · **Auth:** JWT / OAuth2
- **Operación:** Docker · observabilidad y métricas integradas

## Cómo correr

```bash
cp .env.example .env          # ajusta credenciales locales
docker compose up -d          # API, base de datos, cola y workers

# tests
pytest
```

> Detalle de diseño en [`docs/ARCHITECTURE_V2.md`](./docs/ARCHITECTURE_V2.md) y plan de
> pruebas en [`docs/TEST_PLAN.md`](./docs/TEST_PLAN.md).
