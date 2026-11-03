"""Secrets + Gemini model selection. The key and any model override live ONLY in .streamlit/secrets.toml (git-ignored);
nothing here is shown on the UI."""
import json, os, re, threading, time, tomllib
from pathlib import Path

SECRETS = Path(__file__).parent / ".streamlit" / "secrets.toml"


class MissingKeyError(RuntimeError):
    pass


def load_api_key():
    """Read GOOGLE_API_KEY from the secrets file and expose it to the Gemini SDK. Fails loudly if absent."""
    if not SECRETS.exists():
        raise MissingKeyError(f"Secrets file not found: {SECRETS}\nCopy .streamlit/secrets.toml.example to secrets.toml and paste your Gemini key.")
    key = tomllib.loads(SECRETS.read_text()).get("GOOGLE_API_KEY", "").strip()
    if not key or key.startswith("paste"):
        raise MissingKeyError(f"GOOGLE_API_KEY is empty in {SECRETS}. Get a free key at https://aistudio.google.com/apikey")
    os.environ["GOOGLE_API_KEY"] = key          # agno / google-genai read it from the process env
    return key


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
    """Sliding 60-second window shared by every Gemini call in the process, so the free tier's requests-per-minute
    limit is never exceeded: a call waits for a free slot instead of failing with 429."""
    def __init__(self): self.calls, self.lock = [], threading.Lock()

    def wait(self):
        rpm = int(tomllib.loads(SECRETS.read_text()).get("GEMINI_RPM", 5))   # free tier: 5 requests/minute per model
        with self.lock:
            now = time.time(); self.calls = [t for t in self.calls if now - t < 60]
            if len(self.calls) >= rpm:
                time.sleep(max(0.0, 60 - (now - self.calls[0])) + 0.5); now = time.time(); self.calls = self.calls[1:]
            self.calls.append(now)


LIMITER = _RateLimiter()


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
    call = client.models.generate_content
    def limited(*a, **k):
        LIMITER.wait(); return call(*a, **k)
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
    preferred = tomllib.loads(SECRETS.read_text()).get("SCRIBE_MODEL", "").strip()   # optional override
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
