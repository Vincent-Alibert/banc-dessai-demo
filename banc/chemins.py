"""Emplacements locaux du dossier de simulation, sans lecture de fichier d'environnement."""

from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
RUNS = RACINE / "runs"
PERSONAS = RACINE / "personas"
JEUX_DEMO = RACINE / "demo"
PROMPT_JUGE = RACINE / "demo" / "GRILLE.md"
SEED_JEUX_PERSONAS = RACINE / "dbt" / "seeds" / "jeux_personas.csv"
