from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = PROJECT_ROOT / ".runtime"
RUNTIME_ROOT.mkdir(exist_ok=True)
os.environ["PRETIX_CONFIG_FILE"] = str(PROJECT_ROOT / "dev" / "pretix-test.cfg")

from pretix.settings import *  # noqa: E402,F403

DATABASES["default"]["TEST"] = {"NAME": ":memory:"}  # noqa: F405
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
COMPRESS_ENABLED = False
COMPRESS_OFFLINE = False
STORAGES["staticfiles"] = {  # noqa: F405
    "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
}
