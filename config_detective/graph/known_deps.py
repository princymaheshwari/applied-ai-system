"""Known cross-layer dependencies.

This module contains hardcoded mappings of known dependencies between
different layers of the environment:
- Python packages -> OS packages they require
- Python packages -> env vars they read
- OS packages -> env vars that configure them

These mappings allow the graph builder to create edges between nodes
that wouldn't otherwise be connected in the raw snapshot data.

The mappings are necessarily incomplete - they cover common cases that
frequently cause "works on my machine" bugs. The system can still work
without these edges; they just improve suspect scoring accuracy.
"""

from __future__ import annotations


# Python packages that require specific OS packages
# Format: python_package_name -> [os_package_names]
PYTHON_TO_OS_DEPS: dict[str, list[str]] = {
    # Cryptography and SSL
    "cryptography": ["libssl", "libssl3", "libssl1.1", "openssl", "libffi"],
    "pyopenssl": ["libssl", "libssl3", "openssl"],
    "ssl": ["libssl", "openssl"],

    # Database drivers
    "psycopg2": ["libpq", "libpq5", "libpq-dev", "postgresql-libs"],
    "psycopg2-binary": ["libpq", "libpq5"],
    "psycopg": ["libpq", "libpq5"],
    "mysqlclient": ["libmysqlclient", "mysql-client", "mariadb-connector-c"],
    "pymysql": [],  # Pure Python, no OS deps
    "sqlite3": ["libsqlite3", "sqlite"],

    # Scientific / ML
    "numpy": ["libopenblas", "libblas", "liblapack"],
    "scipy": ["libopenblas", "libblas", "liblapack", "libgfortran"],
    "pandas": ["libopenblas"],
    "scikit-learn": ["libopenblas", "libgomp"],
    "torch": ["libgomp", "libcudart", "libnccl"],
    "tensorflow": ["libcudart", "libcudnn"],

    # Image processing
    "pillow": ["libjpeg", "libpng", "libtiff", "libwebp", "zlib"],
    "opencv-python": ["libgl1", "libglib2.0", "libsm6", "libxrender1", "libxext6"],

    # XML/HTML
    "lxml": ["libxml2", "libxslt"],
    "defusedxml": ["libxml2"],

    # Compression
    "zlib": ["zlib1g", "zlib"],
    "bz2": ["libbz2"],
    "lzma": ["liblzma"],

    # System
    "cffi": ["libffi"],
    "pycairo": ["libcairo2"],
    "pygobject": ["libgirepository"],

    # Network
    "pycurl": ["libcurl", "libcurl4"],

    # Audio/Video
    "pyaudio": ["portaudio"],
    "soundfile": ["libsndfile"],
}

# Python packages that read specific env vars
# Format: python_package_name -> [env_var_names]
PYTHON_TO_ENV_DEPS: dict[str, list[str]] = {
    # SSL/TLS configuration
    "requests": [
        "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
    ],
    "urllib3": ["SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY"],
    "httpx": ["SSL_CERT_FILE", "HTTP_PROXY", "HTTPS_PROXY"],
    "aiohttp": ["SSL_CERT_FILE"],
    "cryptography": ["OPENSSL_CONF"],

    # Database connections
    "psycopg2": ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSSLMODE"],
    "psycopg": ["PGHOST", "PGPORT", "PGDATABASE", "PGUSER"],
    "sqlalchemy": ["DATABASE_URL"],

    # Locale and encoding
    "locale": ["LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE"],
    "codecs": ["PYTHONIOENCODING"],

    # Timezone
    "datetime": ["TZ"],
    "pytz": ["TZ"],
    "dateutil": ["TZ"],
    "pendulum": ["TZ"],

    # Proxy
    "pip": ["HTTP_PROXY", "HTTPS_PROXY", "PIP_INDEX_URL"],

    # Cloud SDKs
    "boto3": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "AWS_PROFILE"],
    "google-cloud-core": ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
    "azure-identity": ["AZURE_CLIENT_ID", "AZURE_TENANT_ID", "AZURE_CLIENT_SECRET"],

    # ML/AI
    "torch": ["CUDA_VISIBLE_DEVICES", "CUDA_HOME"],
    "tensorflow": ["CUDA_VISIBLE_DEVICES", "TF_CPP_MIN_LOG_LEVEL"],
    "transformers": ["HF_HOME", "TRANSFORMERS_CACHE", "HF_TOKEN"],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
}

# OS packages that are configured by specific env vars
# Format: os_package_name_pattern -> [env_var_names]
OS_TO_ENV_DEPS: dict[str, list[str]] = {
    "libssl": ["OPENSSL_CONF", "SSL_CERT_FILE", "SSL_CERT_DIR"],
    "openssl": ["OPENSSL_CONF", "SSL_CERT_FILE", "SSL_CERT_DIR"],
    "libpq": ["PGHOST", "PGPORT", "PGSSLMODE"],
    "glibc": ["LANG", "LC_ALL", "LC_CTYPE"],
    "libc": ["LANG", "LC_ALL"],
    "locales": ["LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE"],
    "tzdata": ["TZ"],
    "ca-certificates": ["SSL_CERT_FILE", "SSL_CERT_DIR"],
}

# Known problematic version combinations
# Format: (package_a, version_pattern_a, package_b, version_pattern_b) -> description
# This is used for detecting known incompatibilities
KNOWN_INCOMPATIBILITIES: list[tuple[str, str, str, str, str]] = [
    # OpenSSL 3.x vs 1.1.x breaks many things
    ("cryptography", ">=41.0.0", "libssl", "1.1.*", 
     "cryptography 41+ requires OpenSSL 3.x, but libssl 1.1.x is installed"),
    
    # Python version mismatches
    ("numpy", ">=1.24.0", "python", "3.8.*",
     "numpy 1.24+ requires Python 3.9+"),
    
    # Alpine/musl vs glibc
    ("tensorflow", "*", "musl", "*",
     "TensorFlow does not work on Alpine/musl systems"),
]

# Categories for deltas (used for grouping in reports)
DELTA_CATEGORIES: dict[str, list[str]] = {
    "ssl": ["ssl", "openssl", "libssl", "crypto", "tls", "certificate", "ca-cert"],
    "locale": ["locale", "lang", "lc_", "language", "encoding", "utf", "ascii", "unicode"],
    "timezone": ["tz", "timezone", "tzdata", "zoneinfo"],
    "database": ["pg", "postgres", "mysql", "mariadb", "sqlite", "libpq", "database"],
    "python": ["python", "pip", "venv", "virtualenv", "pyenv"],
    "node": ["node", "npm", "yarn", "pnpm"],
    "libc": ["glibc", "musl", "libc6", "libc-bin"],
    "network": ["proxy", "http_proxy", "https_proxy", "curl", "wget"],
    "ml": ["cuda", "cudnn", "torch", "tensorflow", "gpu"],
}


def get_python_os_deps(package_name: str) -> list[str]:
    """Get OS package dependencies for a Python package."""
    # Normalize package name (lowercase, underscores to hyphens)
    normalized = package_name.lower().replace("_", "-")
    return PYTHON_TO_OS_DEPS.get(normalized, [])


def get_python_env_deps(package_name: str) -> list[str]:
    """Get env var dependencies for a Python package."""
    normalized = package_name.lower().replace("_", "-")
    return PYTHON_TO_ENV_DEPS.get(normalized, [])


def get_os_env_deps(package_name: str) -> list[str]:
    """Get env vars that configure an OS package."""
    normalized = package_name.lower()
    
    # Check for exact match first
    if normalized in OS_TO_ENV_DEPS:
        return OS_TO_ENV_DEPS[normalized]
    
    # Check for partial matches (e.g., "libssl3" matches "libssl")
    for pattern, env_vars in OS_TO_ENV_DEPS.items():
        if pattern in normalized or normalized in pattern:
            return env_vars
    
    return []


def categorize_delta(name: str) -> str | None:
    """Determine the category of a delta based on its name."""
    name_lower = name.lower()
    
    for category, patterns in DELTA_CATEGORIES.items():
        for pattern in patterns:
            if pattern in name_lower:
                return category
    
    return None
