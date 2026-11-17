"""Load ARM settings before application configuration is read.

Only the connection bootstrap comes from the process environment. ARM values
replace matching environment variables; unspecified keys keep their environment
fallback. Failed or empty responses leave the environment unchanged.
"""
from __future__ import annotations

import logging
import os
import ssl
from threading import Lock

from .lib import ArmUtils

LOGGER = logging.getLogger(__name__)
_LOCK = Lock()
_SOURCE: str | None = None


def _tls_context() -> ssl.SSLContext:
    verify = os.environ.get("ARM_TLS_VERIFY", "true").strip().lower()
    if verify not in {"true", "1", "yes", "on", "false", "0", "no", "off"}:
        raise ValueError("ARM_TLS_VERIFY must be a boolean")
    ctx = ssl.create_default_context(
        cafile=os.environ.get("ARM_CA_FILE") or None,
        capath=os.environ.get("ARM_CA_PATH") or None,
    )
    if verify in {"false", "0", "no", "off"}:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    cert = os.environ.get("ARM_CLIENT_CERT_FILE")
    key = os.environ.get("ARM_CLIENT_KEY_FILE")
    if key and not cert:
        raise ValueError("ARM_CLIENT_KEY_FILE requires ARM_CLIENT_CERT_FILE")
    if cert:
        ctx.load_cert_chain(
            certfile=cert,
            keyfile=key or None,
            password=os.environ.get("ARM_CLIENT_KEY_PASSWORD") or None,
        )
    return ctx


def load_environment(default_app_name: str) -> str:
    """Fetch once per process via ArmUtils, then make values available to getenv.

    Returns 'arm' or 'env'. No settings values or credentials are logged. An
    explicit empty ARM value overrides env; missing ARM keys keep env/defaults.
    Restart the process to reload settings, including after an ARM outage.
    """
    global _SOURCE
    with _LOCK:
        if _SOURCE is not None:
            return _SOURCE
        app_name = os.environ.get("ARM_APP_NAME", default_app_name).strip() or default_app_name
        try:
            options = {}
            url = os.environ.get("ARM_SETTINGS_URL", "").strip()
            if url:
                options["url"] = url
            arm = ArmUtils(ctx=_tls_context(), app_name=app_name, **options)
            rows = arm.getArmSettings()  # Sends params={"sfObjekt": app_name}.
            if not rows:
                raise ValueError("ARM returned no settings")
            settings: dict[str, str] = {}
            for row in rows:
                key = row.SF_NASTAVITEV
                value = row.SF_NASTAVITEV_VREDNOST
                if not isinstance(key, str) or not key.strip() or "=" in key or "\0" in key:
                    raise ValueError("Invalid ARM setting key")
                key = key.strip()
                if key in settings:
                    raise ValueError("Duplicate ARM setting key")
                if value is None:
                    value = ""
                if not isinstance(value, str) or "\0" in value:
                    raise ValueError("Invalid ARM setting value")
                settings[key] = value
            # Validate the entire response before applying any of it.
            os.environ.update(settings)
            _SOURCE = "arm"
            LOGGER.info("Application settings loaded from ARM: app=%s count=%s", app_name, len(settings))
        except Exception as exc:
            _SOURCE = "env"
            LOGGER.warning(
                "ARM settings unavailable: app=%s error_type=%s; using environment and application defaults",
                app_name, type(exc).__name__,
            )
        return _SOURCE
