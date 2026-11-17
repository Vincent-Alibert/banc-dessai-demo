"""Initialisation rejouable des volumes de démo puis migration, sans modifier le dossier de code."""

from pathlib import Path
import os
import sys

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))


def main():
    if os.getuid() != 0:
        raise SystemExit("[X] initialisation réservée au service init-banc")
    for dossier in (RACINE / "runs", RACINE / "dbt/target", RACINE / "dbt/logs", Path("/opt/airflow/logs")):
        dossier.mkdir(parents=True, exist_ok=True)
        os.chown(dossier, 50000, 0)
        os.chmod(dossier, 0o775)
    os.setgroups([])
    os.setgid(0)
    os.setuid(50000)
    from entrepot.migrer import main as migrer

    return migrer()


if __name__ == "__main__":
    sys.exit(main())
