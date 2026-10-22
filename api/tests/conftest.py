"""Configuration partagée des tests.

Les tests marqués `integration` touchent le réseau (appel Gemini réel) : ils ne sont
pas exécutés par défaut, il faut les demander explicitement avec `-m integration`.
"""

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: appel réel au fournisseur (réseau + clé), exclu par défaut",
    )
