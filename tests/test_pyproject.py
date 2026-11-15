"""
pyproject.toml et uv.lock, seule source des dependances : aucun requirements*.txt, l'image Airflow installe depuis le
verrou, et la meme version de uv sert partout (poste, CI GitHub, Dockerfile).

Lancer : python -m unittest tests.test_pyproject -v   (ou pytest)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
import unittest
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")
VERSION_UV = "0.12.9"  # celle qui a ecrit uv.lock


def projet() -> dict:
    with (RACINE / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def toutes_les_dependances(p: dict) -> list[str]:
    return [*p["project"]["dependencies"], *(d for g in p["dependency-groups"].values() for d in g)]


class TestPyproject(unittest.TestCase):
    def test_toutes_les_versions_directes_epinglees(self) -> None:
        for dep in toutes_les_dependances(projet()):
            self.assertRegex(dep, r"^[A-Za-z0-9_.\-]+(\[[a-z,]+\])?==\d+(\.\d+)*$", dep)

    def test_python_aligne_sur_l_image_airflow(self) -> None:
        p = projet()
        self.assertEqual(p["project"]["requires-python"], "~=3.12.0")
        self.assertEqual(p["tool"]["ruff"]["target-version"], "py312")
        self.assertIn("-python3.12@", (RACINE / "docker" / "airflow" / "Dockerfile").read_text(encoding="utf-8"))

    def test_airflow_hors_des_dependances(self) -> None:
        toutes = toutes_les_dependances(projet())
        self.assertFalse([d for d in toutes if "airflow" in d.lower()], "Airflow vit dans son image, pas ici")

    def test_testpaths(self) -> None:
        self.assertEqual(projet()["tool"]["pytest"]["ini_options"]["testpaths"], ["tests"])

    def test_aucun_requirements(self) -> None:
        restants = []
        for dossier, sous, fichiers in os.walk(RACINE):
            sous[:] = [s for s in sous if s not in {".git", ".venv", ".uv-cache"}]
            restants += [f for f in fichiers if f.startswith("requirements") and f.endswith(".txt")]
        self.assertEqual(restants, [], "pyproject.toml et uv.lock sont la seule source des dependances")

    def test_image_installe_depuis_le_verrou(self) -> None:
        dockerfile = (RACINE / "docker" / "airflow" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY --chown=airflow:0 pyproject.toml uv.lock", dockerfile)
        # le uv copie (/bin/uv), pas celui de l'image Airflow ; un groupe par venv
        self.assertRegex(dockerfile, r"UV_PROJECT_ENVIRONMENT=/opt/banc-venv /bin/uv sync --frozen --no-dev ")
        self.assertRegex(dockerfile, r"UV_PROJECT_ENVIRONMENT=/opt/dbt-venv /bin/uv sync --frozen .*--only-group dbt ")
        self.assertEqual(len(re.findall(r"\buv sync\b", dockerfile)), 2)
        self.assertNotIn("pip install", dockerfile)

    def test_meme_version_de_uv_partout(self) -> None:
        dockerfile = (RACINE / "docker" / "airflow" / "Dockerfile").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"ghcr\.io/astral-sh/uv:([0-9.]+)", dockerfile), [VERSION_UV])
        self.assertIn("/uv /bin/uv", dockerfile)
        ci = (RACINE / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'version: "([0-9.]+)"', ci), [VERSION_UV])
        self.assertEqual(projet()["tool"]["uv"]["required-version"], f"=={VERSION_UV}")  # poste : uv refuse un autre

    @unittest.skipUnless(UV, "uv absent")
    def test_verrou_a_jour(self) -> None:
        r = subprocess.run(
            [UV, "lock", "--check", "--offline"], cwd=RACINE, capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(r.returncode, 0, f"uv.lock en retard sur pyproject.toml (uv lock) : {r.stderr}")


if __name__ == "__main__":
    unittest.main()
