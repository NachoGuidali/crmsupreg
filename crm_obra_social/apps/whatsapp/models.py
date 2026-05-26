from django.conf import settings
from django.core.cache import cache
from django.db import models


class ConfiguracionWhatsApp(models.Model):
    """Singleton — stores WhatsApp Cloud API credentials editable from the UI."""
    access_token = models.CharField(max_length=500, blank=True, verbose_name='Access Token')
    phone_number_id = models.CharField(max_length=50, blank=True, verbose_name='Phone Number ID')
    business_account_id = models.CharField(max_length=50, blank=True, verbose_name='Business Account ID')
    app_secret = models.CharField(max_length=200, blank=True, verbose_name='App Secret')
    webhook_verify_token = models.CharField(max_length=100, default='verify_token_default', verbose_name='Webhook Verify Token')

    class Meta:
        verbose_name = 'Configuración WhatsApp'
        verbose_name_plural = 'Configuración WhatsApp'

    def __str__(self):
        return 'Configuración WhatsApp'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete('whatsapp_config')

    @classmethod
    def get_config(cls):
        config = cache.get('whatsapp_config')
        if config is None:
            try:
                obj = cls.objects.get(pk=1)
                config = {
                    'access_token': obj.access_token,
                    'phone_number_id': obj.phone_number_id,
                    'business_account_id': obj.business_account_id,
                    'app_secret': obj.app_secret,
                    'webhook_verify_token': obj.webhook_verify_token,
                }
            except cls.DoesNotExist:
                config = {}
            cache.set('whatsapp_config', config, 300)
        return config

    @classmethod
    def get_setting(cls, key):
        """Read a setting from DB first, fall back to Django settings."""
        db_val = cls.get_config().get(key)
        if db_val:
            return db_val
        settings_map = {
            'access_token': 'WHATSAPP_ACCESS_TOKEN',
            'phone_number_id': 'WHATSAPP_PHONE_NUMBER_ID',
            'business_account_id': 'WHATSAPP_BUSINESS_ACCOUNT_ID',
            'app_secret': 'WHATSAPP_APP_SECRET',
            'webhook_verify_token': 'WHATSAPP_WEBHOOK_VERIFY_TOKEN',
        }
        return getattr(settings, settings_map.get(key, ''), '')


class Conversacion(models.Model):
    telefono = models.CharField(max_length=20, unique=True, db_index=True)
    nombre_contacto = models.CharField(max_length=200, blank=True)
    lead = models.OneToOneField(
        'leads.Lead',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='conversacion_whatsapp',
    )
    agente = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='conversaciones',
    )
    ultimo_mensaje_at = models.DateTimeField(null=True, blank=True)
    mensajes_no_leidos = models.PositiveIntegerField(default=0)
    ventana_activa = models.BooleanField(default=False)
    ventana_expira_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Conversación'
        verbose_name_plural = 'Conversaciones'
        ordering = ['-ultimo_mensaje_at']

    def __str__(self):
        return f'{self.nombre_contacto or self.telefono}'

    def get_display_name(self):
        return self.nombre_contacto or self.telefono


class Mensaje(models.Model):
    TIPO_TEXTO = 'text'
    TIPO_IMAGEN = 'image'
    TIPO_DOCUMENTO = 'document'
    TIPO_AUDIO = 'audio'
    TIPO_VIDEO = 'video'
    TIPO_PLANTILLA = 'template'
    TIPO_INTERACTIVO = 'interactive'
    TIPO_CHOICES = [
        (TIPO_TEXTO, 'Texto'),
        (TIPO_IMAGEN, 'Imagen'),
        (TIPO_DOCUMENTO, 'Documento'),
        (TIPO_AUDIO, 'Audio'),
        (TIPO_VIDEO, 'Video'),
        (TIPO_PLANTILLA, 'Plantilla HSM'),
        (TIPO_INTERACTIVO, 'Interactivo'),
    ]

    DIR_ENTRANTE = 'in'
    DIR_SALIENTE = 'out'
    DIR_CHOICES = [
        (DIR_ENTRANTE, 'Entrante'),
        (DIR_SALIENTE, 'Saliente'),
    ]

    STATUS_PENDIENTE = 'pending'
    STATUS_ENVIADO = 'sent'
    STATUS_ENTREGADO = 'delivered'
    STATUS_LEIDO = 'read'
    STATUS_FALLIDO = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDIENTE, 'Pendiente'),
        (STATUS_ENVIADO, 'Enviado'),
        (STATUS_ENTREGADO, 'Entregado'),
        (STATUS_LEIDO, 'Leído'),
        (STATUS_FALLIDO, 'Fallido'),
    ]

    conversacion = models.ForeignKey(Conversacion, on_delete=models.CASCADE, related_name='mensajes')
    lead = models.ForeignKey('leads.Lead', null=True, blank=True, on_delete=models.SET_NULL, related_name='mensajes_whatsapp')
    whatsapp_message_id = models.CharField(max_length=100, blank=True, db_index=True)
    direccion = models.CharField(max_length=3, choices=DIR_CHOICES)
    tipo = models.CharField(max_length=20, choices=TIPO_CHOICES, default=TIPO_TEXTO)
    contenido = models.TextField(blank=True)
    media_url = models.URLField(blank=True, max_length=1000)
    media_id = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDIENTE)
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    timestamp = models.DateTimeField()
    error_detalle = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Mensaje'
        verbose_name_plural = 'Mensajes'
        ordering = ['timestamp']

    def __str__(self):
        return f'[{self.get_direccion_display()}] {self.conversacion} — {self.timestamp}'


class PlantillaHSM(models.Model):
    CATEGORIA_MARKETING = 'MARKETING'
    CATEGORIA_UTILIDAD = 'UTILITY'
    CATEGORIA_AUTENTICACION = 'AUTHENTICATION'
    CATEGORIA_CHOICES = [
        (CATEGORIA_MARKETING, 'Marketing'),
        (CATEGORIA_UTILIDAD, 'Utilidad'),
        (CATEGORIA_AUTENTICACION, 'Autenticación'),
    ]

    STATUS_PENDIENTE = 'PENDING'
    STATUS_APROBADA = 'APPROVED'
    STATUS_RECHAZADA = 'REJECTED'
    STATUS_CHOICES = [
        (STATUS_PENDIENTE, 'Pendiente'),
        (STATUS_APROBADA, 'Aprobada'),
        (STATUS_RECHAZADA, 'Rechazada'),
    ]

    HEADER_NONE = 'none'
    HEADER_TEXT = 'text'
    HEADER_IMAGE = 'image'
    HEADER_DOCUMENT = 'document'
    HEADER_VIDEO = 'video'
    HEADER_CHOICES = [
        (HEADER_NONE, 'Sin header'),
        (HEADER_TEXT, 'Texto'),
        (HEADER_IMAGE, 'Imagen'),
        (HEADER_DOCUMENT, 'Documento'),
        (HEADER_VIDEO, 'Video'),
    ]

    nombre = models.CharField(max_length=100, unique=True)
    nombre_meta = models.CharField(max_length=100, blank=True, help_text='Nombre en Meta (snake_case, sin espacios). Se genera automáticamente al enviar.')
    categoria = models.CharField(max_length=30, choices=CATEGORIA_CHOICES, default=CATEGORIA_MARKETING)
    idioma = models.CharField(max_length=10, default='es_AR')
    cuerpo = models.TextField(help_text='Texto principal. Usar {{1}}, {{2}}... para variables.')
    variables = models.JSONField(default=list, blank=True, help_text='Lista de nombres de variables en orden: ["nombre", "plan", ...]')
    # Header
    header_tipo = models.CharField(max_length=10, choices=HEADER_CHOICES, default=HEADER_NONE, verbose_name='Tipo de header')
    header_contenido = models.TextField(blank=True, verbose_name='Contenido del header', help_text='Texto del header (si tipo=Texto) o URL del archivo (imagen/video/documento)')
    # Footer
    footer = models.CharField(max_length=60, blank=True, verbose_name='Footer', help_text='Texto del pie de mensaje (máximo 60 caracteres)')
    # Buttons — list of {"tipo": "reply|url|phone", "texto": "...", "valor": "..."}
    botones = models.JSONField(default=list, blank=True, verbose_name='Botones', help_text='[{"tipo":"reply","texto":"Sí, me interesa"},{"tipo":"url","texto":"Ver planes","valor":"https://..."}]')
    # Meta tracking
    meta_template_id = models.CharField(max_length=50, blank=True, verbose_name='ID en Meta')
    ultimo_sync_at = models.DateTimeField(null=True, blank=True, verbose_name='Último sync con Meta')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDIENTE)
    activa = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Plantilla HSM'
        verbose_name_plural = 'Plantillas HSM'
        ordering = ['nombre']

    def __str__(self):
        return f'{self.nombre} ({self.get_status_display()})'

    def preview(self, valores=None):
        """Return template body with variables replaced by given values."""
        text = self.cuerpo
        if valores:
            for i, val in enumerate(valores, start=1):
                text = text.replace(f'{{{{{i}}}}}', str(val))
        return text

    def build_send_components(self, variables_vals=None):
        """Build components array for SENDING this template via Meta API."""
        components = []
        if self.header_tipo != self.HEADER_NONE and self.header_contenido:
            if self.header_tipo == self.HEADER_TEXT:
                components.append({
                    'type': 'header',
                    'parameters': [{'type': 'text', 'text': self.header_contenido}],
                })
            else:
                components.append({
                    'type': 'header',
                    'parameters': [{
                        'type': self.header_tipo,
                        self.header_tipo: {'link': self.header_contenido},
                    }],
                })
        if variables_vals:
            components.append({
                'type': 'body',
                'parameters': [{'type': 'text', 'text': str(v)} for v in variables_vals],
            })
        if self.botones:
            for i, btn in enumerate(self.botones):
                sub_type = 'quick_reply' if btn['tipo'] == 'reply' else btn['tipo']
                components.append({
                    'type': 'button',
                    'sub_type': sub_type,
                    'index': str(i),
                    'parameters': [{'type': 'payload', 'payload': btn.get('valor', btn['texto'])}],
                })
        return components

    def build_create_payload(self):
        """Build components array for CREATING/SUBMITTING this template to Meta."""
        components = []
        if self.header_tipo != self.HEADER_NONE and self.header_contenido:
            if self.header_tipo == self.HEADER_TEXT:
                components.append({'type': 'HEADER', 'format': 'TEXT', 'text': self.header_contenido})
            else:
                fmt = self.header_tipo.upper()
                components.append({
                    'type': 'HEADER',
                    'format': fmt,
                    'example': {'header_url': [self.header_contenido]},
                })
        components.append({'type': 'BODY', 'text': self.cuerpo})
        if self.footer:
            components.append({'type': 'FOOTER', 'text': self.footer})
        if self.botones:
            buttons = []
            for btn in self.botones:
                if btn['tipo'] == 'reply':
                    buttons.append({'type': 'QUICK_REPLY', 'text': btn['texto']})
                elif btn['tipo'] == 'url':
                    buttons.append({'type': 'URL', 'text': btn['texto'], 'url': btn.get('valor', '')})
                elif btn['tipo'] == 'phone':
                    buttons.append({'type': 'PHONE_NUMBER', 'text': btn['texto'], 'phone_number': btn.get('valor', '')})
            if buttons:
                components.append({'type': 'BUTTONS', 'buttons': buttons})
        return components


class BotRespuesta(models.Model):
    """Configurable auto-response rules for incoming WhatsApp messages."""

    TRIGGER_PRIMER_MENSAJE = 'primer_mensaje'
    TRIGGER_PALABRA_CLAVE = 'palabra_clave'
    TRIGGER_CHOICES = [
        (TRIGGER_PRIMER_MENSAJE, 'Primer mensaje del contacto (bienvenida)'),
        (TRIGGER_PALABRA_CLAVE, 'Contiene palabras clave'),
    ]

    RESPUESTA_TEXTO = 'texto'
    RESPUESTA_PLANTILLA = 'plantilla'
    RESPUESTA_INTERACTIVO = 'interactivo'
    RESPUESTA_CHOICES = [
        (RESPUESTA_TEXTO, 'Texto libre'),
        (RESPUESTA_PLANTILLA, 'Plantilla HSM'),
        (RESPUESTA_INTERACTIVO, 'Mensaje con botones'),
    ]

    nombre = models.CharField(max_length=100, verbose_name='Nombre de la regla')
    activa = models.BooleanField(default=True, db_index=True)
    orden = models.PositiveSmallIntegerField(default=0, help_text='Menor número = mayor prioridad')

    # Trigger
    trigger_tipo = models.CharField(max_length=20, choices=TRIGGER_CHOICES, verbose_name='Disparador')
    palabras_clave = models.JSONField(
        default=list, blank=True, verbose_name='Palabras clave',
        help_text='Lista de palabras/frases. Coincidencia parcial, sin distinción de mayúsculas. Ej: ["precio", "info", "quiero"]',
    )

    # Response
    respuesta_tipo = models.CharField(max_length=20, choices=RESPUESTA_CHOICES, default=RESPUESTA_TEXTO)
    respuesta_texto = models.TextField(blank=True, verbose_name='Texto de respuesta')
    respuesta_plantilla = models.ForeignKey(
        'PlantillaHSM', null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name='Plantilla HSM', related_name='bot_reglas',
    )
    respuesta_interactivo_body = models.TextField(blank=True, verbose_name='Cuerpo del mensaje interactivo')
    respuesta_interactivo_botones = models.JSONField(
        default=list, blank=True, verbose_name='Botones',
        help_text='[{"id":"btn_1","title":"Sí, quiero información"}]',
    )

    # Actions on the lead
    accion_estado = models.CharField(
        max_length=20, blank=True, verbose_name='Cambiar estado del lead a',
        help_text='Dejar vacío para no cambiar el estado',
    )
    accion_prioridad = models.CharField(
        max_length=10, blank=True, verbose_name='Cambiar prioridad del lead a',
        help_text='Dejar vacío para no cambiar la prioridad',
    )

    # Conditions
    solo_si_sin_agente = models.BooleanField(
        default=False, verbose_name='Solo si no tiene agente asignado',
    )
    solo_primera_vez = models.BooleanField(
        default=True, verbose_name='Solo responder una vez por conversación',
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Regla de bot'
        verbose_name_plural = 'Reglas de bot WhatsApp'
        ordering = ['orden', 'nombre']

    def __str__(self):
        estado = '✅' if self.activa else '⏸'
        return f'{estado} {self.nombre}'

    def matches(self, message_text: str) -> bool:
        """Return True if this rule's trigger matches the given message text."""
        if self.trigger_tipo == self.TRIGGER_PALABRA_CLAVE:
            text_lower = message_text.lower()
            return any(kw.lower() in text_lower for kw in (self.palabras_clave or []))
        return False  # PRIMER_MENSAJE is checked externally


class LogBotRespuesta(models.Model):
    """Tracks bot responses per conversation to prevent duplicate triggers."""
    conversacion = models.ForeignKey(Conversacion, on_delete=models.CASCADE, related_name='bot_logs')
    regla = models.ForeignKey(BotRespuesta, on_delete=models.CASCADE, related_name='logs')
    respondido_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('conversacion', 'regla')]


class LogAPIWhatsApp(models.Model):
    endpoint = models.CharField(max_length=200)
    method = models.CharField(max_length=10)
    request_body = models.TextField(blank=True)
    response_status = models.IntegerField(null=True)
    response_body = models.TextField(blank=True)
    duracion_ms = models.IntegerField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    exitoso = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Log API WhatsApp'
        verbose_name_plural = 'Logs API WhatsApp'
        ordering = ['-created_at']
