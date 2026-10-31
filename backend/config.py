from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(dotenv_path=None, override=False, **kwargs):
        if not dotenv_path or not Path(dotenv_path).exists():
            return False
        for raw_line in Path(dotenv_path).read_text(encoding='utf-8').splitlines():
            line = raw_line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if override or key not in os.environ:
                os.environ[key] = val
        return True

ROOT = Path(__file__).resolve().parent.parent
# The user explicitly configures this local prototype via .env. Prefer it to
# inherited desktop/terminal variables so an old key cannot shadow a new one.
load_dotenv(ROOT / '.env', override=True)


@dataclass(frozen=True)
class Settings:
    provider: str = os.getenv('DECISION_PROVIDER', 'jev').strip().lower()
    api_key: str = os.getenv('ANTHROPIC_API_KEY', '')
    model: str = os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-5-5')
    max_tokens: int = int(os.getenv('ANTHROPIC_MAX_TOKENS', '2000'))
    timeout: float = float(os.getenv('ANTHROPIC_TIMEOUT_SECONDS', '25'))
    retries: int = int(os.getenv('ANTHROPIC_MAX_RETRIES', '1'))
    jev_api_key: str = os.getenv('JEV_API_KEY', '').strip() or os.getenv('TYPESAFE_API_KEY', '').strip()
    jev_base_url: str = os.getenv('JEV_BASE_URL', 'https://api.typesafe.ai/v1').rstrip('/')
    jev_model: str = os.getenv('JEV_MODEL', 'jev-latest')
    jev_timeout: float = float(os.getenv('JEV_TIMEOUT_SECONDS', '25'))
    jev_retries: int = int(os.getenv('JEV_MAX_RETRIES', '1'))
    jev_min_confidence: float = float(os.getenv('JEV_MIN_CONFIDENCE', '0.7'))
    max_rounds: int = int(os.getenv('NEGOTIATION_MAX_ROUNDS', '2'))
    deadline: float = float(os.getenv('NEGOTIATION_DEADLINE_SECONDS', '120'))
    database: str = os.getenv('DATABASE_PATH', './data/coordination.sqlite3')
    frontend_origin: str = os.getenv('FRONTEND_ORIGIN', 'http://localhost:5173')

    @property
    def selected_api_key(self) -> str:
        return self.jev_api_key if self.provider == 'jev' else self.api_key

    @property
    def selected_model(self) -> str:
        return self.jev_model if self.provider == 'jev' else self.model

    @property
    def provider_label(self) -> str:
        return 'Jev' if self.provider == 'jev' else 'Claude'

    def database_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else ROOT / p

    def __post_init__(self):
        if self.provider not in ('claude', 'jev'):
            raise ValueError('DECISION_PROVIDER must be claude or jev.')
        if self.max_rounds not in (1, 2):
            raise ValueError('NEGOTIATION_MAX_ROUNDS must be 1 or 2 for this challenge.')
        if self.deadline <= 0 or self.timeout <= 0 or self.max_tokens <= 0:
            raise ValueError('Timeouts and token budget must be positive.')
        if not 0 <= self.retries <= 2:
            raise ValueError('ANTHROPIC_MAX_RETRIES must be between 0 and 2.')
        if not 0 <= self.jev_retries <= 2 or self.jev_timeout <= 0:
            raise ValueError('JEV_MAX_RETRIES must be 0–2 and JEV_TIMEOUT_SECONDS positive.')
        if not .5 < self.jev_min_confidence <= 1:
            raise ValueError('JEV_MIN_CONFIDENCE must be greater than 0.5 and at most 1.')
        parsed = urlparse(self.jev_base_url)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('JEV_BASE_URL must be a base URL without credentials, query or fragment.')
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1')):
            raise ValueError('JEV_BASE_URL must use HTTPS (HTTP is allowed only for local development).')
