#!/usr/bin/env python3
"""Simple setup script for development."""

from setuptools import setup, find_packages

setup(
    name="pixell-runtime",
    version="0.1.0",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.11",
    install_requires=[
        "fastapi>=0.109.0",
        "uvicorn[standard]>=0.27.0",
        "pydantic>=2.5.0",
        "pydantic-settings>=2.1.0",
        "httpx>=0.26.0",
        "boto3>=1.34.0",
        "pyyaml>=6.0.1",
        "prometheus-client>=0.19.0",
        "python-jose[cryptography]>=3.3.0",
        "python-multipart>=0.0.6",
        "structlog>=24.1.0",
        "aiofiles>=23.2.1",
        "cryptography>=42.0.0",
    ],
    entry_points={
        "console_scripts": [
            "pixell-runtime=pixell_runtime.main:run",
        ],
    },
)