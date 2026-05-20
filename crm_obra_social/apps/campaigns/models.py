from django.conf import settings
from django.db import models


class Campana(models.Model):
    STATUS_BORRADOR = 'borrador'
    STATUS_PROGRAMADA = 'programada'
    STATUS_EN_EJECUCION = 'en_ejecucion'
    STATUS_COMPLETADA = 'completada'
    STATUS_CHOICES = [
        (STATUS_BORRADOR, 'Borrador'),
        (STATUS_PROGRAMADA, 'Programada'),
        (STATUS_EN_EJECUCION, 'En ejecución'),
        (STATUS_COMPLETADA, 'Completada'),
    ]

    MODO_SEGMENTO = 'segmento'
    MODO_MANUAL = 'manual'
    MODO_CHOICES = [
        (MODO_SEGMENTO, 'Por segmento (filtros automáticos)'),
        (MODO_MANUAL, 'Selección manual de contactos'),
    ]

    nombre = models.CharField(max_length=200)
    plantilla = models.ForeignKey('whatsapp.PlantillaHSM', on_delete=models.PROTECT)
    modo_seleccion = models.CharField(max_length=10, choices=MODO_CHOICES, default=MODO_SEGMENTO)
    # Segment filters stored as JSON for flexibility
    filtros_segmento = models.JSONField(default=dict, blank=True, help_text='Filtros de segmento: estado, plan_id, provincia, dias_sin_contacto')
    # Manual contact selection: list of lead PKs
    contactos_ids = models.JSONField(
        default=list, blank=True,
        verbose_name='Contactos seleccionados',
        help_text='Lista de IDs de leads seleccionados manualmente.',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_BORRADOR)
    fecha_programada = models.DateTimeField(null=True, blank=True)
    creado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    # Variable mapping: [{"tipo": "campo|fijo", "valor": "nombre_completo|..."}]
    variables_mapping = models.JSONField(
        default=list, blank=True,
        verbose_name='Mapeo de variables',
        help_text='Define cómo se rellenan {{1}}, {{2}}... de la plantilla para cada lead.',
    )
    # Stats
    total_destinatarios = models.PositiveIntegerField(default=0)
    enviados = models.PositiveIntegerField(default=0)
    entregados = models.PositiveIntegerField(default=0)
    leidos = models.PositiveIntegerField(default=0)
    errores = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Campaña'
        verbose_name_plural = 'Campañas'
        ordering = ['-created_at']

    def __str__(self):
        return self.nombre

    def get_segment_queryset(self):
        from datetime import timedelta
        from apps.leads.models import Lead
        from django.utils import timezone

        # Manual selection: use exact list of IDs
        if self.modo_seleccion == self.MODO_MANUAL and self.contactos_ids:
            return Lead.objects.filter(pk__in=self.contactos_ids, telefono__startswith='+')

        # Segment-based filtering
        qs = Lead.objects.filter(telefono__startswith='+')
        f = self.filtros_segmento
        if f.get('estado'):
            qs = qs.filter(estado=f['estado'])
        if f.get('plan_id'):
            qs = qs.filter(plan_interes_id=f['plan_id'])
        if f.get('provincia'):
            qs = qs.filter(provincia__icontains=f['provincia'])
        if f.get('dias_sin_contacto'):
            cutoff = timezone.now() - timedelta(days=int(f['dias_sin_contacto']))
            qs = qs.filter(updated_at__lt=cutoff)
        return qs


class CampanaLog(models.Model):
    STATUS_ENVIADO = 'enviado'
    STATUS_ERROR = 'error'
    STATUS_CHOICES = [
        (STATUS_ENVIADO, 'Enviado'),
        (STATUS_ERROR, 'Error'),
    ]

    campana = models.ForeignKey(Campana, on_delete=models.CASCADE, related_name='logs')
    lead = models.ForeignKey('leads.Lead', on_delete=models.SET_NULL, null=True)
    telefono = models.CharField(max_length=20)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES)
    whatsapp_message_id = models.CharField(max_length=100, blank=True)
    error_detalle = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Log de campaña'
        verbose_name_plural = 'Logs de campaña'
