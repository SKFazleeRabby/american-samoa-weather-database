"""Never log provider credentials, even from HTTP client request messages."""
import logging
import re

from as_db.web.config import settings


class SecretFilter(logging.Filter):
    def __init__(self, secrets):
        super().__init__()
        self.secrets = [s for s in secrets if s]

    def filter(self, record):
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, '[REDACTED]')
        record.msg = re.sub(r'([?&]token=)[^&\s\"]+', r'\1[REDACTED]', message, flags=re.I)
        record.args = ()
        return True


def configure_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    env = settings()
    for handler in logging.getLogger().handlers:
        handler.addFilter(SecretFilter([env.get('SYNOPTIC_TOKEN'), env.get('HCDP_API')]))
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
