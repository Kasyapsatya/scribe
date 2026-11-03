"""Secrets + Gemini model selection. The team key and settings live ONLY in secrets (`.streamlit/secrets.toml`, git-ignored, or the
hosting platform's secrets, exposed as environment variables). A teammate may supply their own key for their browser session: it is held
in memory for that session only (USER_KEY), never written anywhere, and takes precedence over the team key. Nothing here is shown on the UI."""
import contextvars, hashlib, json, os, re, threading, time, tomllib
from pathlib import Path

SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"
USER_KEY = contextvars.ContextVar("user_key", default="")      # set by the app at the start of every run of a session


class MissingKeyError(RuntimeError):
    pass


def secret(name, default=None):
    """A setting from the secrets file, else from the environment (how hosted platforms expose secrets)."""
    try: value = tomllib.loads(SECRETS.read_text()).get(name) if SECRETS.exists() else None
    except tomllib.TOMLDecodeError: value = None
    return value if value not in (None, "") else os.environ.get(name, default)


def team_key():
    key = str(secret("GOOGLE_API_KEY", "")).strip()
    return "" if key.startswith("paste") else key


def load_api_key():
    """The key for this session: the user's own if given, else the team key. Fails loudly if neither exists."""
    key = USER_KEY.get().strip() or team_key()
    if not key:
        raise MissingKeyError("No Gemini API key: add GOOGLE_API_KEY to .streamlit/secrets.toml (or the app's secrets). "
                              "Get a free key at https://aistudio.google.com/apikey")
    return key


def key_id():
    """Short fingerprint of the active key, so quota cool-downs and rate limits are tracked per key (never the key itself)."""
    try: return hashlib.sha256(load_api_key().encode()).hexdigest()[:10]
    except MissingKeyError: return "none"


# ---------------- model selection: never hardcode a model that Google can retire ----------------
CACHE = SECRETS.parent / "model_cache.json"                     # remembers the working model → no API calls at startup
CACHE_HOURS = 24
TIMEOUT_MS = 20_000                                              # every Gemini HTTP call gives up after 20 s
_FLASH = re.compile(r"^gemini-(\d+(?:\.\d+)?)-flash$")             # stable Flash only (no -lite / -preview / -exp)
_SPECIAL = re.compile(r"lite|image|tts|audio|live|embed|vision|robotics")   # special-purpose variants: never use
_MODEL = None


class QuotaExhausted(RuntimeError):
    pass


def is_quota_error(e):
    t = str(e)
    return "429" in t or "RESOURCE_EXHAUSTED" in t or "quota" in t.lower()


class _RateLimiter:
    """Sliding 60-second window per API key, so a key's free-tier requests-per-minute limit is never exceeded:
    a call waits for a free slot instead of failing with 429."""
    def __init__(self): self.calls, self.lock = [], threading.Lock()

    def wait(self):
        rpm = int(secret("GEMINI_RPM", 5))                                    # free tier: 5 requests/minute per model
        with self.lock:
            now = time.time(); self.calls = [t for t in self.calls if now - t < 60]
            if len(self.calls) >= rpm:
                time.sleep(max(0.0, 60 - (now - self.calls[0])) + 0.5); now = time.time(); self.calls = self.calls[1:]
            self.calls.append(now)


LIMITERS = {}                                                             # key fingerprint → limiter


def retry_after(e):
    """Seconds Google asks us to wait (per-minute limit), or None for a daily limit / other error."""
    t = str(e)
    if "PerDay" in t: return None
    m = re.search(r"retry in ([\d.]+)s", t) or re.search(r'"retryDelay":\s*"(\d+)s"', t)
    return float(m.group(1)) if m else None


def make_client():
    """One place that builds the Gemini client: hard timeout, no silent retry loops, shared rate limiter."""
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=load_api_key(),
                          http_options=types.HttpOptions(timeout=TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
    call, limiter = client.models.generate_content, LIMITERS.setdefault(key_id(), _RateLimiter())
    def limited(*a, **k):
        limiter.wait(); return call(*a, **k)
    client.models.generate_content = limited
    return client


def cached_model():
    """Model remembered from the last successful check (None if absent or older than CACHE_HOURS). No network."""
    try:
        c = json.loads(CACHE.read_text())
        return c["model"] if time.time() - c["checked_at"] < CACHE_HOURS * 3600 else None
    except Exception:
        return None


def forget_model():
    """Call when the remembered model stops working (retired / no tool calling) so the next run re-checks."""
    global _MODEL
    _MODEL = None
    CACHE.unlink(missing_ok=True)


def _candidates(available, preferred):
    """Preferred model first (if listed), then stable Flash models newest-first, then any other general Flash."""
    stable = sorted((n for n in available if _FLASH.match(n)), key=lambda n: float(_FLASH.match(n).group(1)), reverse=True)
    other = sorted((n for n in available if "flash" in n and n not in stable and not _SPECIAL.search(n)), reverse=True)
    return ([preferred] if preferred in available else []) + [n for n in stable + other if n != preferred]


def _probe(client, name):
    """The model must answer AND make a function call — the Q&A agent depends on tool use."""
    from google.genai import types
    ping = types.FunctionDeclaration(name="ping", description="Health check. Always call this.",
                                     parameters=types.Schema(type="OBJECT", properties={"x": types.Schema(type="STRING")}, required=["x"]))
    cfg = types.GenerateContentConfig(tools=[types.Tool(function_declarations=[ping])],
                                      tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="ANY")))
    r = client.models.generate_content(model=name, contents="Call ping with x='ok'.", config=cfg)
    if not r.function_calls: raise RuntimeError("answered but made no function call")


def resolve_model(max_seconds=60):
    """Return a Gemini model that supports tool calling. Uses the remembered model if fresh (zero API calls);
    otherwise lists models and probes candidates, newest first, within max_seconds.
    Raises QuotaExhausted if every candidate is rate-limited, RuntimeError if none works."""
    global _MODEL
    preferred = str(secret("SCRIBE_MODEL", "")).strip()                       # optional override
    cached = cached_model()
    if _MODEL: return _MODEL
    if cached and (not preferred or preferred == cached):
        _MODEL = cached; return _MODEL
    client = make_client(); t0 = time.time(); tried = []; quota_hits = 0
    available = [m.name.split("/")[-1] for m in client.models.list() if "generateContent" in (m.supported_actions or [])]
    for name in _candidates(available, preferred):
        if time.time() - t0 > max_seconds:
            tried.append(f"stopped after {max_seconds}s"); break
        try:
            _probe(client, name)
            _MODEL = name
            CACHE.write_text(json.dumps(dict(model=name, checked_at=time.time())))
            return name
        except Exception as e:                                   # 404 retired / 400 no tool calling / 429 quota / timeout
            quota_hits += is_quota_error(e)
            tried.append(f"{name}: {str(e)[:110]}")
    if tried and quota_hits == len([t for t in tried if not t.startswith("stopped")]):
        raise QuotaExhausted("Gemini quota exhausted for every candidate model (free tier resets daily).")
    raise RuntimeError("No Gemini Flash model with function calling is available for this key.\n" + "\n".join(tried or ["model list was empty"]))
