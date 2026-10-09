DJANGO_APPS = ["django.contrib.admin", "django.contrib.auth"]
THIRD_PARTY_APPS = [
    "taggit",
    "crispy_forms",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + ["shop"]
INSTALLED_APPS += ["django_ready"]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
]
AUTHENTICATION_BACKENDS = ["allauth.account.auth_backends.AuthenticationBackend"]
REST_FRAMEWORK = {
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
}
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql"}}

USE_L10N = True
DEFAULT_FILE_STORAGE = "storages.backends.s3.S3Storage"
