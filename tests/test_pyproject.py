"""
pyproject.toml et uv.lock, source de verite des dependances. requirements.txt et dbt/requirements.txt, qu'installent
l'image Docker (docker/airflow/Dockerfile), sont generes depuis le verrou
par uv export (commandes dans pyproject.toml) et doivent lui correspondre exactement.

Avec uv (poste, CI GitLab) : comparaison a uv export et verrou a jour (uv lock --check). Sans uv : les
epinglages directs de pyproject.toml doivent figurer dans les fichiers generes.

Lancer : python -m unittest tests.test_pyproject -v   (ou pytest)
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")
# fichier genere : options de uv export (sans -o), comme dans l'en-tete de pyproject.toml
GENERES = {
    "requirements.txt": ["--no-dev"],
    "dbt/requirements.txt": ["--only-group", "dbt"],
}


def projet() -> dict:
    with (RACINE / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def exigences(texte: str) -> list[str]:
    """Lignes d'exigence d'un fichier requirements, dans l'ordre : commentaires (dont les « # via » indentes de uv),
    lignes vides et inclusions « -r » retires."""
    lignes = []
    for ligne in texte.splitlines():
        ligne = ligne.split("#", 1)[0].strip()
        if ligne and not ligne.startswith("-r"):
            lignes.append(ligne)
    return lignes


def epinglages(dependances: list[str]) -> list[str]:
    """« psycopg[binary]==3.3.6 » -> « psycopg==3.3.6 » : uv export ecrit les extras comme des paquets a part."""
    return [d.split("[", 1)[0] + "==" + d.split("==", 1)[1] if "[" in d else d for d in dependances]


class TestPyproject(unittest.TestCase):
    def test_toutes_les_versions_directes_epinglees(self) -> None:
        p = projet()
        for dep in [*p["project"]["dependencies"], *(d for g in p["dependency-groups"].values() for d in g)]:
            self.assertRegex(dep, r"^[A-Za-z0-9_.\-]+(\[[a-z,]+\])?==\d+(\.\d+)*$", dep)

    def test_python_aligne_sur_l_image_airflow(self) -> None:
        p = projet()
        self.assertEqual(p["project"]["requires-python"], "~=3.12.0")
        self.assertEqual(p["tool"]["ruff"]["target-version"], "py312")
        dockerfile = (RACINE / "docker" / "airflow" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("-python3.12@", dockerfile)

    def test_airflow_hors_des_dependances(self) -> None:
        p = projet()
        toutes = [*p["project"]["dependencies"], *(d for g in p["dependency-groups"].values() for d in g)]
        self.assertFalse([d for d in toutes if "airflow" in d.lower()], "Airflow vit dans son image, pas ici")

    def test_testpaths_comme_la_ci(self) -> None:
        self.assertEqual(projet()["tool"]["pytest"]["ini_options"]["testpaths"], ["tests"])

    def test_epinglages_directs_dans_les_fichiers_generes(self) -> None:
        p = projet()
        attendus = {
            "requirements.txt": epinglages(p["project"]["dependencies"]),
            "dbt/requirements.txt": p["dependency-groups"]["dbt"],
        }
        for fichier, deps in attendus.items():
            presents = {e.split(";", 1)[0].strip().lower() for e in exigences((RACINE / fichier).read_text("utf-8"))}
            for dep in deps:
                self.assertIn(dep.lower(), presents, f"{dep} absent de {fichier}")

    @unittest.skipUnless(UV, "uv absent")
    def test_fichiers_generes_identiques_a_uv_export(self) -> None:
        for fichier, options in GENERES.items():
            with self.subTest(fichier=fichier):
                r = subprocess.run(
                    [UV, "export", "--frozen", "--offline", "--no-emit-project", "--no-hashes", *options],
                    cwd=RACINE,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(
                    exigences((RACINE / fichier).read_text("utf-8")),
                    exigences(r.stdout),
                    f"{fichier} a regenerer : uv export --frozen --no-emit-project --no-hashes "
                    f"{' '.join(options)} -o {fichier}",
                )

    @unittest.skipUnless(UV, "uv absent")
    def test_verrou_a_jour(self) -> None:
        r = subprocess.run(
            [UV, "lock", "--check", "--offline"], cwd=RACINE, capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(r.returncode, 0, f"uv.lock en retard sur pyproject.toml (uv lock) : {r.stderr}")


if __name__ == "__main__":
    unittest.main()
