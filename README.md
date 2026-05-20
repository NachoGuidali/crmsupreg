# CRM Obra Social — Sistema de ventas con WhatsApp Business API

CRM para agentes comerciales de obra social argentina. Gestión de leads, pipeline Kanban, integración con Meta WhatsApp Cloud API, cotizaciones con PDF y campañas masivas.

## Stack

- **Backend:** Django 5.1 + Celery + Redis
- **DB:** PostgreSQL 15
- **WhatsApp:** Meta Cloud API (webhook + envío)
- **PDF:** WeasyPrint
- **Frontend:** Django Templates + Bootstrap 5 (server-side rendering)
- **Infraestructura:** Docker + docker-compose

---

## Setup rápido (Docker)

### 1. Clonar y configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con tus credenciales de Meta WhatsApp API
```

### 2. Levantar los servicios

```bash
docker-compose up --build
```

Esto levanta: PostgreSQL, Redis, Django (web), Celery worker, Celery Beat.

### 3. Crear superusuario

```bash
docker-compose exec web python manage.py createsuperuser
```

### 4. Cargar planes de obra social iniciales

```bash
docker-compose exec web python manage.py loaddata apps/leads/fixtures/planes_iniciales.json
```

### 5. Acceder al sistema

- **App:** http://localhost:8000
- **Admin Django:** http://localhost:8000/admin

---

## Variables de entorno (.env)

| Variable | Descripción |
|---|---|
| `DJANGO_SECRET_KEY` | Clave secreta Django (cambiar en producción) |
| `DJANGO_DEBUG` | `True` en desarrollo, `False` en producción |
| `POSTGRES_*` | Credenciales de la base de datos |
| `REDIS_URL` | URL de Redis (ej: `redis://redis:6379/0`) |
| `WHATSAPP_ACCESS_TOKEN` | Token de acceso de Meta |
| `WHATSAPP_PHONE_NUMBER_ID` | ID del número de teléfono en Meta |
| `WHATSAPP_WEBHOOK_VERIFY_TOKEN` | Token para verificar el webhook |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | ID de la cuenta de negocio en Meta |

---

## Configuración del Webhook de WhatsApp

1. En Meta Business Manager → WhatsApp → Configuración → Webhooks
2. URL del webhook: `https://tu-dominio.com/whatsapp/webhook/`
3. Verify Token: el valor de `WHATSAPP_WEBHOOK_VERIFY_TOKEN` en el .env
4. Suscribirse a los eventos: `messages`, `message_deliveries`, `message_reads`

> En desarrollo se puede usar **ngrok** para exponer el servidor local:
> ```bash
> ngrok http 8000
> ```

---

## Módulos del sistema

| Módulo | URL | Descripción |
|---|---|---|
| Dashboard | `/` | Métricas y actividad reciente |
| Leads | `/leads/` | ABM + filtros + CSV |
| Kanban | `/leads/kanban/` | Pipeline drag & drop |
| WhatsApp Inbox | `/whatsapp/inbox/` | Conversaciones activas |
| Plantillas HSM | `/whatsapp/plantillas/` | ABM de plantillas Meta |
| Tareas | `/tareas/` | Lista y agenda |
| Cotizaciones | `/cotizaciones/nueva/` | Cotización con PDF |
| Campañas | `/campanas/` | Envíos masivos |
| Reportes | `/reportes/conversion/` | Funnel y métricas |
| Admin | `/admin/` | Django admin |

---

## Roles de usuario

| Rol | Permisos |
|---|---|
| `superadmin` | Acceso total |
| `supervisor` | Ve todos los leads y agentes, reportes globales |
| `agente` | Solo sus propios leads y conversaciones asignadas |

---

## Celery Beat — Tareas programadas

Configurar en Django Admin → `Periodic Tasks`:

| Tarea | Frecuencia sugerida |
|---|---|
| `apps.tasks.tasks.marcar_tareas_vencidas` | Cada 15 minutos |
| `apps.tasks.tasks.notificar_tareas_proximas` | Cada 10 minutos |
| `apps.whatsapp.tasks.expire_24h_windows` | Cada hora |
| `apps.campaigns.tasks.lanzar_campanas_programadas` | Cada 5 minutos |

---

## Comandos útiles

```bash
# Ejecutar tests
docker-compose exec web python manage.py test apps.leads apps.whatsapp

# Shell de Django
docker-compose exec web python manage.py shell

# Ver logs de Celery
docker-compose logs -f celery

# Migraciones
docker-compose exec web python manage.py makemigrations
docker-compose exec web python manage.py migrate
```

---

## Estructura del proyecto

```
crm_obra_social/
├── config/              # settings, urls, celery, wsgi
├── apps/
│   ├── users/           # Autenticación y perfiles
│   ├── leads/           # Leads, pipeline, historial
│   ├── tasks/           # Tareas y agenda
│   ├── quotes/          # Cotizaciones y PDF
│   ├── whatsapp/        # Meta API, webhook, inbox
│   ├── campaigns/       # Campañas masivas
│   └── reports/         # Dashboard y reportes
├── templates/           # HTML templates
├── static/css/          # Estilos CSS
└── requirements.txt
```
