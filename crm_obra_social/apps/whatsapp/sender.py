import json
import logging
import time

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger('apps.whatsapp')


def _wa(key):
    """Read a WhatsApp credential from DB config (falls back to settings/env)."""
    from .models import ConfiguracionWhatsApp
    return ConfiguracionWhatsApp.get_setting(key)


def _get_headers():
    return {
        'Authorization': f'Bearer {_wa("access_token")}',
        'Content-Type': 'application/json',
    }


def _log_request(endpoint, method, request_body, response, duracion_ms):
    from .models import LogAPIWhatsApp
    try:
        LogAPIWhatsApp.objects.create(
            endpoint=endpoint,
            method=method,
            request_body=json.dumps(request_body) if isinstance(request_body, dict) else str(request_body),
            response_status=response.status_code if response else None,
            response_body=response.text[:5000] if response else '',
            duracion_ms=duracion_ms,
            exitoso=response is not None and response.status_code < 300,
        )
    except Exception:
        pass


def send_text_message(to: str, body: str) -> dict:
    """Send a free-form text message (must be within 24h window)."""
    url = f'{settings.WHATSAPP_API_URL}/{_wa('phone_number_id')}/messages'
    payload = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': to,
        'type': 'text',
        'text': {'preview_url': False, 'body': body},
    }
    start = time.monotonic()
    response = None
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        logger.info('Text message sent to %s', to)
        return data
    except requests.RequestException as e:
        logger.error('Error sending text to %s: %s', to, e)
        raise
    finally:
        _log_request(url, 'POST', payload, response, int((time.monotonic() - start) * 1000))


def send_template_message(to: str, template_name: str, language: str, components: list) -> dict:
    """Send an HSM template message."""
    url = f'{settings.WHATSAPP_API_URL}/{_wa('phone_number_id')}/messages'
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'template',
        'template': {
            'name': template_name,
            'language': {'code': language},
            'components': components,
        },
    }
    start = time.monotonic()
    response = None
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        logger.info('Template "%s" sent to %s', template_name, to)
        return data
    except requests.RequestException as e:
        logger.error('Error sending template "%s" to %s: %s', template_name, to, e)
        raise
    finally:
        _log_request(url, 'POST', payload, response, int((time.monotonic() - start) * 1000))


def send_document_message(to: str, document_url: str, filename: str, caption: str = '') -> dict:
    """Send a document (PDF, etc.) by URL."""
    url = f'{settings.WHATSAPP_API_URL}/{_wa('phone_number_id')}/messages'
    payload = {
        'messaging_product': 'whatsapp',
        'to': to,
        'type': 'document',
        'document': {'link': document_url, 'filename': filename, 'caption': caption},
    }
    start = time.monotonic()
    response = None
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error('Error sending document to %s: %s', to, e)
        raise
    finally:
        _log_request(url, 'POST', payload, response, int((time.monotonic() - start) * 1000))


def send_interactive_message(to: str, body_text: str, buttons: list, header_text: str = '', footer_text: str = '') -> dict:
    """
    Send an interactive quick-reply button message (max 3 buttons).
    buttons: [{"id": "btn_1", "title": "Texto botón"}]
    """
    url = f'{settings.WHATSAPP_API_URL}/{_wa('phone_number_id')}/messages'
    interactive = {
        'type': 'button',
        'body': {'text': body_text},
        'action': {
            'buttons': [
                {'type': 'reply', 'reply': {'id': btn['id'], 'title': btn['title'][:20]}}
                for btn in buttons[:3]
            ],
        },
    }
    if header_text:
        interactive['header'] = {'type': 'text', 'text': header_text}
    if footer_text:
        interactive['footer'] = {'text': footer_text}
    payload = {
        'messaging_product': 'whatsapp',
        'recipient_type': 'individual',
        'to': to,
        'type': 'interactive',
        'interactive': interactive,
    }
    start = time.monotonic()
    response = None
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        logger.info('Interactive message sent to %s', to)
        return data
    except requests.RequestException as e:
        logger.error('Error sending interactive to %s: %s', to, e)
        raise
    finally:
        _log_request(url, 'POST', payload, response, int((time.monotonic() - start) * 1000))


def mark_message_as_read(whatsapp_message_id: str) -> None:
    url = f'{settings.WHATSAPP_API_URL}/{_wa('phone_number_id')}/messages'
    payload = {
        'messaging_product': 'whatsapp',
        'status': 'read',
        'message_id': whatsapp_message_id,
    }
    try:
        requests.post(url, json=payload, headers=_get_headers(), timeout=10)
    except Exception:
        pass


def get_media_url(media_id: str) -> str:
    """Retrieve temporary download URL for a media object."""
    url = f'{settings.WHATSAPP_API_URL}/{media_id}'
    start = time.monotonic()
    response = None
    try:
        response = requests.get(url, headers=_get_headers(), timeout=10)
        response.raise_for_status()
        return response.json().get('url', '')
    except requests.RequestException as e:
        logger.error('Error getting media URL for %s: %s', media_id, e)
        return ''
    finally:
        _log_request(url, 'GET', {}, response, int((time.monotonic() - start) * 1000))


def submit_template_to_meta(plantilla) -> dict:
    """Submit a PlantillaHSM to Meta Business API for approval."""
    if not _wa('business_account_id'):
        raise ValueError('WHATSAPP_BUSINESS_ACCOUNT_ID no está configurado en .env')
    url = f'{settings.WHATSAPP_API_URL}/{_wa('business_account_id')}/message_templates'
    template_name = (plantilla.nombre_meta or plantilla.nombre).lower().replace(' ', '_')
    payload = {
        'name': template_name,
        'category': plantilla.categoria,
        'language': plantilla.idioma,
        'components': plantilla.build_create_payload(),
    }
    start = time.monotonic()
    response = None
    try:
        response = requests.post(url, json=payload, headers=_get_headers(), timeout=30)
        response.raise_for_status()
        data = response.json()
        logger.info('Template "%s" submitted to Meta: %s', template_name, data)
        return data
    except requests.RequestException as e:
        logger.error('Error submitting template "%s": %s', template_name, e)
        if response is not None:
            logger.error('Meta response: %s', response.text)
        raise
    finally:
        _log_request(url, 'POST', payload, response, int((time.monotonic() - start) * 1000))


def sync_template_status_from_meta(plantilla) -> dict:
    """Fetch current status of a template from Meta. Returns the template data dict or {}."""
    if not _wa('business_account_id'):
        raise ValueError('WHATSAPP_BUSINESS_ACCOUNT_ID no está configurado en .env')
    template_name = (plantilla.nombre_meta or plantilla.nombre).lower().replace(' ', '_')
    url = f'{settings.WHATSAPP_API_URL}/{_wa('business_account_id')}/message_templates'
    params = {'name': template_name, 'fields': 'name,status,id,quality_score'}
    start = time.monotonic()
    response = None
    try:
        response = requests.get(url, params=params, headers=_get_headers(), timeout=15)
        response.raise_for_status()
        data = response.json()
        templates = data.get('data', [])
        return templates[0] if templates else {}
    except requests.RequestException as e:
        logger.error('Error syncing template "%s": %s', template_name, e)
        raise
    finally:
        _log_request(url, 'GET', params, response, int((time.monotonic() - start) * 1000))
