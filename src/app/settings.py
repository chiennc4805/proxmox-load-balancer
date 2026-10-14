import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv


_ENV_FILE = Path(__file__).with_name('.env')


@dataclass(frozen=True)
class Settings:
    pve_api_host: str
    pve_api_user: str
    pve_token_name: str
    pve_token_value: str = field(repr=False)
    pve_verify_ssl: bool
    pve_request_timeout_seconds: float

    @property
    def pve_api_base_url(self) -> str:
        host = self.pve_api_host.rstrip('/')

        if not host.startswith(('http://', 'https://')):
            parsed_host = urlsplit(f'//{host}')
            netloc = parsed_host.netloc

            if parsed_host.port is None:
                netloc = f'{netloc}:8006'

            host = urlunsplit(
                ('https', netloc, parsed_host.path, '', '')
            )

        # PVE_API_HOST may be either a host or a complete API base URL.
        if host.endswith('/api2/json'):
            return host

        return f'{host}/api2/json'

    @property
    def pve_authorization(self) -> str:
        return (
            f'PVEAPIToken={self.pve_api_user}!{self.pve_token_name}'
            f'={self.pve_token_value}'
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Environment variables supplied by Docker take precedence over the local
    # development .env file.
    load_dotenv(_ENV_FILE, override=False)

    timeout = float(os.getenv('PVE_REQUEST_TIMEOUT_SECONDS', '30'))
    if timeout <= 0:
        raise ValueError('PVE_REQUEST_TIMEOUT_SECONDS must be positive')

    return Settings(
        pve_api_host=_required_environment('PVE_API_HOST'),
        pve_api_user=_required_environment('PVE_API_USER'),
        pve_token_name=_required_environment('PVE_TOKEN_NAME'),
        pve_token_value=_required_environment('PVE_TOKEN_VALUE'),
        pve_verify_ssl=_boolean_environment('PVE_VERIFY_SSL', default=True),
        pve_request_timeout_seconds=timeout,
    )


def _required_environment(name: str) -> str:
    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(f'Missing required environment variable: {name}')

    return value.strip()


def _boolean_environment(name: str, default: bool) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    normalized_value = value.strip().lower()

    if normalized_value in {'true', '1', 'yes', 'on'}:
        return True

    if normalized_value in {'false', '0', 'no', 'off'}:
        return False

    raise ValueError(f'{name} must be a boolean value')
