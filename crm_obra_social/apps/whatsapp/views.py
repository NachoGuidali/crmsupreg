import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.urls import reverse_lazy

from apps.users.models import User
from .models import Conversacion, Mensaje, PlantillaHSM
from .tasks import process_incoming_message, send_whatsapp_message_task
from .webhook import parse_incoming_webhook, verify_signature

logger = logging.getLogger('apps.whatsapp')


@method_decorator(csrf_exempt, name='dispatch')
class WebhookView(View):
    def get(self, request):
        """Meta verification challenge."""
        mode = request.GET.get('hub.mode')
        token = request.GET.get('hub.verify_token')
        challenge = request.GET.get('hub.challenge')
        if mode == 'subscribe' and token == settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN:
            logger.info('WhatsApp webhook verified successfully.')
            return HttpResponse(challenge, content_type='text/plain')
        return HttpResponse('Forbidden', status=403)

    def post(self, request):
        """Receive and queue incoming messages."""
        sig = request.headers.get('X-Hub-Signature-256', '')
        if not verify_signature(request.body, sig, getattr(settings, 'WHATSAPP_APP_SECRET', '')):
            logger.warning('Invalid webhook signature — request rejected')
            return HttpResponse('Forbidden', status=403)
        try:
            payload = json.loads(request.body)
            logger.debug('Webhook received: %s', json.dumps(payload)[:500])
            messages_data = parse_incoming_webhook(payload)
            for msg_data in messages_data:
                process_incoming_message.delay(msg_data)
        except Exception as e:
            logger.exception('Webhook processing error: %s', e)
        return HttpResponse('OK', status=200)


class InboxView(LoginRequiredMixin, View):
    template_name = 'whatsapp/inbox.html'

    def get(self, request):
        qs = Conversacion.objects.select_related('lead', 'agente')
        if not request.user.can_see_all_leads:
            qs = qs.filter(agente=request.user)
        # Filter by search query
        q = request.GET.get('q', '').strip()
        if q:
            qs = qs.filter(nombre_contacto__icontains=q) | qs.filter(telefono__icontains=q)
        paginator = Paginator(qs, 30)
        page = paginator.get_page(request.GET.get('page'))
        unread_total = Conversacion.objects.filter(mensajes_no_leidos__gt=0).count()
        if not request.user.can_see_all_leads:
            unread_total = Conversacion.objects.filter(
                mensajes_no_leidos__gt=0, agente=request.user
            ).count()
        return render(request, self.template_name, {
            'conversaciones': page,
            'unread_total': unread_total,
            'q': q,
        })


class ConversacionDetailView(LoginRequiredMixin, View):
    template_name = 'whatsapp/conversacion.html'

    def _get_conv(self, request, pk):
        qs = Conversacion.objects.select_related('lead', 'agente')
        if not request.user.can_see_all_leads:
            qs = qs.filter(agente=request.user)
        return get_object_or_404(qs, pk=pk)

    def get(self, request, pk):
        conv = self._get_conv(request, pk)
        Conversacion.objects.filter(pk=pk).update(mensajes_no_leidos=0)
        mensajes_qs = conv.mensajes.order_by('timestamp')
        paginator = Paginator(mensajes_qs, 50)
        page = paginator.get_page(request.GET.get('page', paginator.num_pages))
        plantillas = PlantillaHSM.objects.filter(activa=True, status=PlantillaHSM.STATUS_APROBADA)
        agents = User.objects.filter(is_active=True) if request.user.can_see_all_leads else None
        last_msg = conv.mensajes.order_by('timestamp').last()
        return render(request, self.template_name, {
            'conv': conv,
            'mensajes': page,
            'plantillas': plantillas,
            'agents': agents,
            'last_msg_id': last_msg.pk if last_msg else 0,
        })

    def post(self, request, pk):
        conv = self._get_conv(request, pk)
        action = request.POST.get('action')

        if action == 'send_text':
            body = request.POST.get('body', '').strip()
            if not body:
                messages.error(request, 'El mensaje no puede estar vacío.')
                return redirect('whatsapp:conversacion', pk=pk)
            if not conv.ventana_activa:
                messages.error(request, 'La ventana de 24hs está cerrada. Usá una plantilla HSM.')
                return redirect('whatsapp:conversacion', pk=pk)
            msg = Mensaje.objects.create(
                conversacion=conv,
                lead=conv.lead,
                direccion=Mensaje.DIR_SALIENTE,
                tipo=Mensaje.TIPO_TEXTO,
                contenido=body,
                status=Mensaje.STATUS_PENDIENTE,
                enviado_por=request.user,
                timestamp=timezone.now(),
            )
            send_whatsapp_message_task.delay(msg.pk)
            Conversacion.objects.filter(pk=pk).update(ultimo_mensaje_at=timezone.now())

        elif action == 'send_template':
            from .sender import send_template_message
            plantilla_id = request.POST.get('plantilla_id')
            if not plantilla_id:
                messages.error(request, 'Seleccioná una plantilla.')
                return redirect('whatsapp:conversacion', pk=pk)
            plantilla = get_object_or_404(PlantillaHSM, pk=plantilla_id)
            variables_vals = [
                request.POST.get(f'var_{i + 1}', '')
                for i in range(len(plantilla.variables))
            ]
            components = plantilla.build_send_components(variables_vals if any(variables_vals) else None)
            try:
                result = send_template_message(
                    conv.telefono,
                    plantilla.nombre_meta or plantilla.nombre,
                    plantilla.idioma,
                    components,
                )
                wam_id = result.get('messages', [{}])[0].get('id', '')
                Mensaje.objects.create(
                    conversacion=conv,
                    lead=conv.lead,
                    direccion=Mensaje.DIR_SALIENTE,
                    tipo=Mensaje.TIPO_PLANTILLA,
                    contenido=plantilla.preview(variables_vals),
                    whatsapp_message_id=wam_id,
                    status=Mensaje.STATUS_ENVIADO,
                    enviado_por=request.user,
                    timestamp=timezone.now(),
                )
                Conversacion.objects.filter(pk=pk).update(ultimo_mensaje_at=timezone.now())
                messages.success(request, 'Plantilla enviada.')
            except Exception as e:
                messages.error(request, f'Error al enviar la plantilla: {e}')

        elif action == 'send_interactive':
            if not conv.ventana_activa:
                messages.error(request, 'La ventana de 24hs está cerrada. Usá una plantilla HSM.')
                return redirect('whatsapp:conversacion', pk=pk)
            body_text = request.POST.get('interactive_body', '').strip()
            header_text = request.POST.get('interactive_header', '').strip()
            footer_text = request.POST.get('interactive_footer', '').strip()
            btn_titles = [t.strip() for t in request.POST.getlist('btn_title') if t.strip()]
            if not body_text or not btn_titles:
                messages.error(request, 'El cuerpo y al menos un botón son requeridos.')
                return redirect('whatsapp:conversacion', pk=pk)
            buttons = [{'id': f'btn_{i}', 'title': title} for i, title in enumerate(btn_titles[:3])]
            from .sender import send_interactive_message
            try:
                result = send_interactive_message(conv.telefono, body_text, buttons, header_text, footer_text)
                wam_id = result.get('messages', [{}])[0].get('id', '')
                btn_display = ' | '.join(f'[{b["title"]}]' for b in buttons)
                contenido_display = body_text + '\n' + btn_display
                Mensaje.objects.create(
                    conversacion=conv,
                    lead=conv.lead,
                    direccion=Mensaje.DIR_SALIENTE,
                    tipo=Mensaje.TIPO_INTERACTIVO,
                    contenido=contenido_display,
                    whatsapp_message_id=wam_id,
                    status=Mensaje.STATUS_ENVIADO,
                    enviado_por=request.user,
                    timestamp=timezone.now(),
                )
                Conversacion.objects.filter(pk=pk).update(ultimo_mensaje_at=timezone.now())
                messages.success(request, 'Mensaje con botones enviado.')
            except Exception as e:
                messages.error(request, f'Error al enviar mensaje interactivo: {e}')

        elif action == 'assign_agent' and request.user.can_see_all_leads:
            agente_id = request.POST.get('agente_id')
            conv.agente_id = agente_id or None
            conv.save(update_fields=['agente_id'])
            messages.success(request, 'Agente asignado.')

        return redirect('whatsapp:conversacion', pk=pk)


class ConversacionMessagesAPIView(LoginRequiredMixin, View):
    """JSON polling endpoint: returns messages newer than since_id."""

    def get(self, request, pk):
        qs = Conversacion.objects.all()
        if not request.user.can_see_all_leads:
            qs = qs.filter(agente=request.user)
        conv = get_object_or_404(qs, pk=pk)
        since_id = int(request.GET.get('since_id', 0))
        nuevos = conv.mensajes.filter(pk__gt=since_id).order_by('timestamp')
        if nuevos.exists():
            Conversacion.objects.filter(pk=pk).update(mensajes_no_leidos=0)
        data = []
        for msg in nuevos:
            data.append({
                'id': msg.pk,
                'direccion': msg.direccion,
                'tipo': msg.tipo,
                'contenido': msg.contenido,
                'media_url': msg.media_url,
                'media_id': msg.media_id,
                'status': msg.status,
                'timestamp': msg.timestamp.strftime('%d/%m %H:%M'),
            })
        conv.refresh_from_db(fields=['ventana_activa', 'ventana_expira_at'])
        return JsonResponse({
            'mensajes': data,
            'ventana_activa': conv.ventana_activa,
            'ventana_expira_at': conv.ventana_expira_at.isoformat() if conv.ventana_expira_at else None,
        })


class InboxUpdatesAPIView(LoginRequiredMixin, View):
    """JSON polling endpoint: returns unread conversation counts for the inbox."""

    def get(self, request):
        qs = Conversacion.objects.filter(mensajes_no_leidos__gt=0)
        if not request.user.can_see_all_leads:
            qs = qs.filter(agente=request.user)
        unread_total = qs.count()
        conv_ids = list(qs.values_list('id', flat=True))
        return JsonResponse({'unread_total': unread_total, 'conv_ids': conv_ids})


class BotReglaListView(LoginRequiredMixin, View):
    template_name = 'whatsapp/bot_list.html'

    def get(self, request):
        from .models import BotRespuesta
        reglas = BotRespuesta.objects.all()
        plantillas = PlantillaHSM.objects.filter(activa=True, status=PlantillaHSM.STATUS_APROBADA)
        return render(request, self.template_name, {'reglas': reglas, 'plantillas': plantillas})


class BotReglaToggleView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from .models import BotRespuesta
        regla = get_object_or_404(BotRespuesta, pk=pk)
        regla.activa = not regla.activa
        regla.save(update_fields=['activa'])
        return JsonResponse({'activa': regla.activa})


class BotReglaCreateView(LoginRequiredMixin, View):
    template_name = 'whatsapp/bot_form.html'

    def get(self, request):
        from .models import BotRespuesta
        from apps.leads.models import Lead
        return render(request, self.template_name, {
            'plantillas': PlantillaHSM.objects.filter(activa=True, status=PlantillaHSM.STATUS_APROBADA),
            'trigger_choices': BotRespuesta.TRIGGER_CHOICES,
            'respuesta_choices': BotRespuesta.RESPUESTA_CHOICES,
            'estado_choices': Lead.ESTADO_CHOICES,
            'prioridad_choices': Lead.PRIORIDAD_CHOICES,
        })

    def post(self, request):
        from .models import BotRespuesta
        from apps.leads.models import Lead
        import json as _json
        err, regla = _save_bot_regla(request.POST, None)
        if err:
            messages.error(request, err)
            return render(request, self.template_name, {
                'plantillas': PlantillaHSM.objects.filter(activa=True, status=PlantillaHSM.STATUS_APROBADA),
                'trigger_choices': BotRespuesta.TRIGGER_CHOICES,
                'respuesta_choices': BotRespuesta.RESPUESTA_CHOICES,
                'estado_choices': Lead.ESTADO_CHOICES,
                'prioridad_choices': Lead.PRIORIDAD_CHOICES,
                'data': request.POST,
            })
        messages.success(request, 'Regla de bot creada.')
        return redirect('whatsapp:bot_list')


class BotReglaUpdateView(LoginRequiredMixin, View):
    template_name = 'whatsapp/bot_form.html'

    def get(self, request, pk):
        from .models import BotRespuesta
        from apps.leads.models import Lead
        regla = get_object_or_404(BotRespuesta, pk=pk)
        return render(request, self.template_name, {
            'regla': regla,
            'plantillas': PlantillaHSM.objects.filter(activa=True, status=PlantillaHSM.STATUS_APROBADA),
            'trigger_choices': BotRespuesta.TRIGGER_CHOICES,
            'respuesta_choices': BotRespuesta.RESPUESTA_CHOICES,
            'estado_choices': Lead.ESTADO_CHOICES,
            'prioridad_choices': Lead.PRIORIDAD_CHOICES,
        })

    def post(self, request, pk):
        from .models import BotRespuesta
        from apps.leads.models import Lead
        regla = get_object_or_404(BotRespuesta, pk=pk)
        err, regla = _save_bot_regla(request.POST, regla)
        if err:
            messages.error(request, err)
            return redirect('whatsapp:bot_update', pk=pk)
        messages.success(request, 'Regla actualizada.')
        return redirect('whatsapp:bot_list')


class BotReglaDeleteView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from .models import BotRespuesta
        regla = get_object_or_404(BotRespuesta, pk=pk)
        regla.delete()
        messages.success(request, 'Regla eliminada.')
        return redirect('whatsapp:bot_list')


def _save_bot_regla(data, instance):
    """Returns (error_string|None, instance)."""
    from .models import BotRespuesta
    import json as _json
    nombre = data.get('nombre', '').strip()
    if not nombre:
        return 'El nombre es requerido.', None
    trigger = data.get('trigger_tipo', '')
    if trigger not in dict(BotRespuesta.TRIGGER_CHOICES):
        return 'Disparador inválido.', None

    if instance is None:
        instance = BotRespuesta()

    instance.nombre = nombre
    instance.activa = data.get('activa') == 'on'
    instance.orden = int(data.get('orden', 0) or 0)
    instance.trigger_tipo = trigger

    raw_kw = data.get('palabras_clave_raw', '').strip()
    instance.palabras_clave = [k.strip() for k in raw_kw.splitlines() if k.strip()] if raw_kw else []

    instance.respuesta_tipo = data.get('respuesta_tipo', BotRespuesta.RESPUESTA_TEXTO)
    instance.respuesta_texto = data.get('respuesta_texto', '').strip()

    plantilla_id = data.get('respuesta_plantilla')
    instance.respuesta_plantilla = PlantillaHSM.objects.filter(pk=plantilla_id).first() if plantilla_id else None

    instance.respuesta_interactivo_body = data.get('respuesta_interactivo_body', '').strip()
    raw_btns = data.get('respuesta_interactivo_botones', '[]').strip()
    try:
        instance.respuesta_interactivo_botones = _json.loads(raw_btns) if raw_btns else []
    except _json.JSONDecodeError:
        instance.respuesta_interactivo_botones = []

    instance.accion_estado = data.get('accion_estado', '')
    instance.accion_prioridad = data.get('accion_prioridad', '')
    instance.solo_si_sin_agente = data.get('solo_si_sin_agente') == 'on'
    instance.solo_primera_vez = data.get('solo_primera_vez') == 'on'
    instance.save()
    return None, instance


class PlantillaListView(LoginRequiredMixin, ListView):
    model = PlantillaHSM
    template_name = 'whatsapp/plantilla_list.html'
    context_object_name = 'plantillas'
    paginate_by = 25


class PlantillaCreateView(LoginRequiredMixin, CreateView):
    model = PlantillaHSM
    template_name = 'whatsapp/plantilla_form.html'
    fields = ('nombre', 'nombre_meta', 'categoria', 'idioma', 'cuerpo', 'variables',
              'header_tipo', 'header_contenido', 'footer', 'botones', 'activa')
    success_url = reverse_lazy('whatsapp:plantilla_list')

    def form_valid(self, form):
        messages.success(self.request, 'Plantilla creada. Podés enviarla a Meta para aprobación.')
        return super().form_valid(form)


class PlantillaUpdateView(LoginRequiredMixin, UpdateView):
    model = PlantillaHSM
    template_name = 'whatsapp/plantilla_form.html'
    fields = ('nombre', 'nombre_meta', 'categoria', 'idioma', 'cuerpo', 'variables',
              'header_tipo', 'header_contenido', 'footer', 'botones', 'activa', 'status')
    success_url = reverse_lazy('whatsapp:plantilla_list')

    def form_valid(self, form):
        messages.success(self.request, 'Plantilla actualizada.')
        return super().form_valid(form)


class PlantillaDeleteView(LoginRequiredMixin, DeleteView):
    model = PlantillaHSM
    template_name = 'whatsapp/plantilla_confirm_delete.html'
    success_url = reverse_lazy('whatsapp:plantilla_list')


class PlantillaPreviewView(LoginRequiredMixin, View):
    """AJAX: preview a template with given variable values."""

    def post(self, request, pk):
        plantilla = get_object_or_404(PlantillaHSM, pk=pk)
        data = json.loads(request.body)
        valores = data.get('valores', [])
        return JsonResponse({'preview': plantilla.preview(valores)})


class PlantillaSubmitView(LoginRequiredMixin, View):
    """Submit a template to Meta Business API for approval."""

    def post(self, request, pk):
        plantilla = get_object_or_404(PlantillaHSM, pk=pk)
        from .sender import submit_template_to_meta
        try:
            result = submit_template_to_meta(plantilla)
            meta_id = result.get('id', '')
            meta_status = result.get('status', '').upper()
            if meta_id:
                plantilla.meta_template_id = meta_id
            if meta_status in dict(PlantillaHSM.STATUS_CHOICES):
                plantilla.status = meta_status
            if not plantilla.nombre_meta:
                plantilla.nombre_meta = (plantilla.nombre).lower().replace(' ', '_')
            plantilla.ultimo_sync_at = timezone.now()
            plantilla.save()
            messages.success(
                request,
                f'Plantilla enviada a Meta correctamente. '
                f'Estado: {plantilla.get_status_display()}. '
                f'Meta recibirá la solicitud y notificará cuando esté aprobada.'
            )
        except Exception as e:
            messages.error(request, f'Error al enviar a Meta: {e}')
        return redirect('whatsapp:plantilla_list')


class PlantillaSyncView(LoginRequiredMixin, View):
    """Sync a single template status from Meta."""

    def post(self, request, pk):
        plantilla = get_object_or_404(PlantillaHSM, pk=pk)
        from .sender import sync_template_status_from_meta
        try:
            data = sync_template_status_from_meta(plantilla)
            if data:
                new_status = data.get('status', '').upper()
                if new_status in dict(PlantillaHSM.STATUS_CHOICES):
                    plantilla.status = new_status
                if data.get('id'):
                    plantilla.meta_template_id = data['id']
                plantilla.ultimo_sync_at = timezone.now()
                plantilla.save()
                messages.success(request, f'Estado actualizado: {plantilla.get_status_display()}')
            else:
                messages.warning(request, 'Meta no devolvió datos. Verificá que el nombre_meta coincida con el de Meta Business Manager.')
        except Exception as e:
            messages.error(request, f'Error al consultar Meta: {e}')
        return redirect('whatsapp:plantilla_list')


class IniciarConversacionView(LoginRequiredMixin, View):
    """Create a Conversacion for a lead and redirect to the chat."""

    def post(self, request, lead_pk):
        from apps.leads.models import Lead
        lead = get_object_or_404(Lead, pk=lead_pk)

        if not lead.telefono or not lead.telefono.startswith('+'):
            messages.error(request, 'El lead no tiene un número válido en formato internacional (+...).')
            return redirect('leads:detail', pk=lead_pk)

        conv, created = Conversacion.objects.get_or_create(
            telefono=lead.telefono,
            defaults={
                'lead': lead,
                'nombre_contacto': lead.nombre_completo,
            }
        )
        if not created and conv.lead_id is None:
            conv.lead = lead
            conv.nombre_contacto = conv.nombre_contacto or lead.nombre_completo
            conv.save(update_fields=['lead', 'nombre_contacto'])

        if created:
            messages.success(
                request,
                f'Conversación creada con {lead.nombre_completo}. '
                'La ventana de 24 hs está cerrada — usá una plantilla HSM para el primer mensaje.'
            )
        else:
            messages.info(request, 'Ya existe una conversación con este contacto.')

        return redirect('whatsapp:conversacion', pk=conv.pk)
