"""Private mail settings shared by the routine's composition and send stages."""
import os
import re
from pathlib import Path

DEFAULT_ENV = '/etc/illoura-routine/mail.env'


def _settings():
    path = Path(os.environ.get('ILLOURA_MAIL_ENV', DEFAULT_ENV))
    values = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, separator, value = line.partition('=')
            if not separator:
                raise ValueError('Mail configuration requires KEY=VALUE lines')
            values[key.strip()] = value.strip()
    values.update({key: value for key, value in os.environ.items()
                   if key.startswith('ILLOURA_MAIL_')})
    return values


def load_mail_config():
    values = _settings()
    addresses = []
    for key in ('ILLOURA_MAIL_SENDER', 'ILLOURA_MAIL_RECIPIENT'):
        value = values.get(key, '').strip()
        # One plain address only: reject display names, lists and header injection.
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", value):
            raise ValueError('Missing or invalid mail configuration: ' + key)
        addresses.append(value)
    return addresses[0].lower(), addresses[1]


def load_link_id():
    value = _settings().get('ILLOURA_MAIL_LINK_ID', '').strip()
    if not value:
        raise ValueError('Missing mail configuration: ILLOURA_MAIL_LINK_ID')
    return value
