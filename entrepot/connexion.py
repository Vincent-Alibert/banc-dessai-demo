"""Connexion au Postgres isolé de démonstration ; aucun fichier .env n'est lu."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

from banc.garde import exiger_simulation


def dsn() -> str:
    exiger_simulation()
    valeur = os.environ.get("BANC_DB_URL", "postgresql://banc:demo-banc-local@127.0.0.1:5432/banc")
    cible = urlsplit(valeur)
    if cible.scheme not in ("postgresql", "postgres") or cible.hostname not in (
        "postgres-banc",
        "127.0.0.1",
        "localhost",
    ):
        raise SystemExit("[X] seule la base Postgres locale de démonstration est autorisée")
    if cible.path != "/banc" or cible.username != "banc" or cible.query or cible.fragment:
        raise SystemExit("[X] connexion attendue : rôle banc, base banc, sans options de redirection")
    return valeur


def connecter(**kwargs):
    valeur = dsn()
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("[X] pilote Postgres absent ; utiliser la pile Docker fournie") from exc
    return psycopg.connect(valeur, **kwargs)
