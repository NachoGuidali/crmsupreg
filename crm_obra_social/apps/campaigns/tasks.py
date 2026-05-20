import logging
import time

from celery import shared_task

logger = logging.getLogger('apps.campaigns')

RATE_LIMIT_PER_MINUTE = 800  # Safely under Meta's 1000/min limit

LEAD_FIELD_MAP = {
    'nombre_completo': lambda l: l.nombre_completo or '',
    'email': lambda l: l.email or '',
    'plan': lambda l: str(l.plan_interes) if l.plan_interes else '',
    'localidad': lambda l: l.localidad or '',
    'provincia': lambda l: l.provincia or '',
    'telefono': lambda l: l.telefono or '',
}


def _build_variables_for_lead(lead, variables_mapping: list) -> list:
    """Resolve template variable values for a specific lead."""
    vals = []
    for mapping in variables_mapping:
        tipo = mapping.get('tipo', 'fijo')
        valor = mapping.get('valor', '')
        if tipo == 'campo':
            vals.append(str(LEAD_FIELD_MAP[valor](lead)) if valor in LEAD_FIELD_MAP else '')
        elif tipo == 'extra':
            # datos_extra column from CSV/Excel import
            vals.append(str((lead.datos_extra or {}).get(valor, '')))
        else:
            vals.append(valor)
    return vals


@shared_task(bind=True)
def ejecutar_campana(self, campana_id: int):
    """Send bulk template messages for a campaign, respecting Meta rate limits."""
    from .models import Campana, CampanaLog
    from apps.whatsapp.sender import send_template_message

    campana = Campana.objects.select_related('plantilla').get(pk=campana_id)
    campana.status = Campana.STATUS_EN_EJECUCION
    campana.save(update_fields=['status'])

    leads = list(campana.get_segment_queryset().select_related('plan_interes'))
    campana.total_destinatarios = len(leads)
    campana.save(update_fields=['total_destinatarios'])

    plantilla = campana.plantilla
    variables_mapping = campana.variables_mapping or []
    enviados = 0
    errores = 0
    interval = 60.0 / RATE_LIMIT_PER_MINUTE

    for lead in leads:
        try:
            variables_vals = _build_variables_for_lead(lead, variables_mapping)
            components = plantilla.build_send_components(variables_vals if variables_vals else None)
            result = send_template_message(
                to=lead.telefono,
                template_name=plantilla.nombre_meta or plantilla.nombre,
                language=plantilla.idioma,
                components=components,
            )
            wam_id = result.get('messages', [{}])[0].get('id', '')
            CampanaLog.objects.create(
                campana=campana,
                lead=lead,
                telefono=lead.telefono,
                status=CampanaLog.STATUS_ENVIADO,
                whatsapp_message_id=wam_id,
            )
            enviados += 1
            logger.info('Campaign %d: sent to %s', campana_id, lead.telefono)
        except Exception as e:
            CampanaLog.objects.create(
                campana=campana,
                lead=lead,
                telefono=lead.telefono,
                status=CampanaLog.STATUS_ERROR,
                error_detalle=str(e),
            )
            errores += 1
            logger.error('Campaign %d: error sending to %s: %s', campana_id, lead.telefono, e)
        time.sleep(interval)

    campana.enviados = enviados
    campana.errores = errores
    campana.status = Campana.STATUS_COMPLETADA
    campana.save(update_fields=['enviados', 'errores', 'status', 'updated_at'])
    return f'Campaña {campana_id}: {enviados} enviados, {errores} errores.'


@shared_task
def lanzar_campanas_programadas():
    """Launch scheduled campaigns that are due. Runs every 5 minutes via Celery Beat."""
    from .models import Campana
    from django.utils import timezone
    now = timezone.now()
    campanas = Campana.objects.filter(status=Campana.STATUS_PROGRAMADA, fecha_programada__lte=now)
    for c in campanas:
        ejecutar_campana.delay(c.pk)
        logger.info('Launched scheduled campaign %d: %s', c.pk, c.nombre)
