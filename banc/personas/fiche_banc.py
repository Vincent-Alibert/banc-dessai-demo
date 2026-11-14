"""
Fiche persona du banc : la fiche de Vincent, plus ce que le banc ajoute A COTE.

On garde les dix axes de Vincent tels quels (schema_fiche.py, mêmes champs) et on ajoute
le pilier dominant comme champ du banc, HORS du vecteur d'axes. Le vecteur reste donc
comparable avec l'autre banc, et version_axes ne bouge pas.

Une fiche du banc est un fichier JSON dans personas/, valide a la lecture.
"""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass, field

import re

from banc import chemins
from banc.personas.schema_fiche import Donnees, FichePersona, VecteurAxes

PILIERS = ("FOCUS", "CONNECTION", "RESILIENCE", "CLARITY")

DOSSIER_PERSONAS = chemins.PERSONAS

# Plafond de mots PAR MESSAGE selon le style d'ecriture : ecrit dans le prompt de role et applique
# par le moteur (coupe propre si depassement), pour ne pas dependre du modele.
PLAFONDS_MOTS: dict[str, int] = {
    "telegraphique": 25,
    "courant": 40,
    "emotionnel": 80,
    "verbeux": 120,
}


def plafond_mots(style_ecriture: str) -> int:
    """Plafond de COUPE, applique par le moteur."""
    return PLAFONDS_MOTS[str(style_ecriture)]


# Plafond DEMANDE au modele, sous le plafond de coupe pour le style verbeux : demande trop haut,
# le modele ne conclut jamais de lui-meme et le banc coupe chaque message.
PLAFONDS_DEMANDES: dict[str, int] = {**PLAFONDS_MOTS, "verbeux": 90}


def plafond_demande(style_ecriture: str) -> int:
    return PLAFONDS_DEMANDES[str(style_ecriture)]


@dataclass(kw_only=True)
class FicheBanc(Donnees):
    """
    identifiant : slug stable, sert de nom de fichier et d'identifiant de run.
    axes        : le vecteur de Vincent, fige a la main (pas tire).
    fiche       : la fiche de Vincent, generee par le modele a partir des axes.
    pilier_dominant : champ du banc, hors vecteur, pour la grille de notation.
    contraintes : limites explicites imposees au prompt de role (ex. : Lucas,
                  aucune idee suicidaire). Verifiees presentes dans system_prompt.
    """

    version_banc: str = "1.0"
    identifiant: str
    axes: VecteurAxes
    fiche: FichePersona
    pilier_dominant: str
    contraintes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", self.identifiant):
            raise ValueError("identifiant de persona invalide")
        if self.pilier_dominant not in PILIERS:
            raise ValueError("pilier dominant inconnu")
        if self.fiche.identite.age < 18:
            raise ValueError("persona mineur : retiré du banc")
        if self.fiche.style.longueur_moyenne_mots <= 0 or not 0 <= self.fiche.style.taux_fautes <= 1:
            raise ValueError("style de fiche invalide")
        if self.fiche.mineur != self.axes.mineur:
            raise ValueError("fiche.mineur et axes.mineur divergent")
        if self.axes.mineur:
            raise ValueError("persona mineur : retire du banc")
        if self.axes.adversarial:
            raise ValueError("persona adversarial : campagne de securite, pas celle-ci")
        for c in self.contraintes:
            if c not in self.fiche.system_prompt:
                raise ValueError(f"contrainte absente du system_prompt : « {c} »")

    def ecrire(self, dossier: Path = DOSSIER_PERSONAS) -> Path:
        dossier.mkdir(exist_ok=True)
        chemin = dossier / f"{self.identifiant}.json"
        chemin.write_text(self.model_dump_json(indent=2, exclude_none=True) + "\n", encoding="utf-8")
        return chemin


def lire(chemin: Path) -> FicheBanc:
    """Lit et VALIDE une fiche. Une fiche invalide ne se charge pas, point."""
    return FicheBanc.model_validate_json(chemin.read_text(encoding="utf-8"))


def lire_toutes(dossier: Path = DOSSIER_PERSONAS) -> list[FicheBanc]:
    return [lire(p) for p in sorted(dossier.glob("*.json"))]


if __name__ == "__main__":
    for f in lire_toutes():
        a = f.axes
        print(f"{f.identifiant:<12} {f.fiche.identite.prenom}, {f.fiche.identite.age} ans — "
              f"{a.position} · {a.motif_entree} · {a.resistance} · {a.style_ecriture} · "
              f"{a.densite_emotionnelle} · {a.cas_sensible} · pilier {f.pilier_dominant}")
