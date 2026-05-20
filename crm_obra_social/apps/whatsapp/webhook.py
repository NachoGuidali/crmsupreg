import hashlib
import hmac as hmac_module
import json
import logging

from django.utils import timezone

logger = logging.getLogger('apps.whatsapp')


def verify_signature(body: bytes, sig_header: str, app_secret: str) -> bool:
    """
    Verify Meta webhook X-Hub-Signature-256 header.
    Returns True if valid. If app_secret is empty, logs a warning and returns True
    (allows development without secret configured).
    """
    if not app_secret:
        logger.warning('WHATSAPP_APP_SECRET not set — skipping webhook signature verification')
        return True
    if not sig_header:
        return False
    try:
        expected = 'sha256=' + hmac_module.new(
            app_secret.encode('utf-8'),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac_module.compare_digest(expected, sig_header)
    except Exception:
        return False


def parse_incoming_webhook(payload: dict) -> list:
    """
    Parse Meta webhook payload and return list of message dicts.
    Each dict: from_phone, message_id, type, content, media_id, timestamp, contact_name.
    """
    messages_data = []
    try:
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                for msg in value.get('messages', []):
                    messages_data.append({
                        'from_phone': msg.get('from'),
                        'message_id': msg.get('id'),
                        'type': msg.get('type'),
                        'content': _extract_content(msg),
                        'media_id': _extract_media_id(msg),
                        'timestamp': timezone.datetime.fromtimestamp(
                            int(msg.get('timestamp', 0)), tz=timezone.utc
                        ),
                        'contact_name': _extract_contact_name(value, msg.get('from')),
                    })
                for status in value.get('statuses', []):
                    _process_status_update(status)
    except Exception as e:
        logger.exception('Error parsing webhook payload: %s', e)
    return messages_data


def _extract_content(msg: dict) -> str:
    msg_type = msg.get('type')
    if msg_type == 'text':
        return msg.get('text', {}).get('body', '')
    if msg_type == 'image':
        return msg.get('image', {}).get('caption', '')
    if msg_type == 'document':
        return msg.get('document', {}).get('filename', '') or msg.get('document', {}).get('caption', '')
    if msg_type == 'audio':
        return '[Audio]'
    if msg_type == 'video':
        return msg.get('video', {}).get('caption', '') or '[Video]'
    if msg_type == 'sticker':
        return '[Sticker]'
    if msg_type == 'location':
        loc = msg.get('location', {})
        return f'[Ubicación: {loc.get("latitude")}, {loc.get("longitude")}]'
    if msg_type == 'button':
        return msg.get('button', {}).get('text', '')
    if msg_type == 'interactive':
        reply = msg.get('interactive', {})
        return (
            reply.get('button_reply', {}).get('title', '')
            or reply.get('list_reply', {}).get('title', '')
        )
    return f'[{msg_type}]'


def _extract_media_id(msg: dict) -> str:
    msg_type = msg.get('type')
    for key in ('image', 'document', 'audio', 'video', 'sticker'):
        if msg_type == key:
            return msg.get(key, {}).get('id', '')
    return ''


def _extract_contact_name(value: dict, phone: str) -> str:
    for contact in value.get('contacts', []):
        if contact.get('wa_id') == phone or contact.get('wa_id') == (phone or '').lstrip('+'):
            return contact.get('profile', {}).get('name', '')
    return ''


def _process_status_update(status: dict):
    from .models import Mensaje
    msg_id = status.get('id')
    new_status = status.get('status')
    status_map = {'sent': 'sent', 'delivered': 'delivered', 'read': 'read', 'failed': 'failed'}
    mapped = status_map.get(new_status)
    if msg_id and mapped:
        Mensaje.objects.filter(whatsapp_message_id=msg_id).update(status=mapped)
