"""Structure et contrats d'orchestration : contrôles statiques, pas un runtime Airflow/Docker réel."""

from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock

from infra.verifier_sources import verifier

try:
    import yaml
except ImportError:  # dépendance optionnelle : les contrôles YAML sont alors signalés non exécutés
    yaml = None

RACINE = Path(__file__).resolve().parents[1]


class Structure(unittest.TestCase):
    def test_compilation_imports_graphe_dbt_yaml_et_sources(self):
        bilan = verifier()
        self.assertFalse(bilan["erreurs"], json.dumps(bilan, ensure_ascii=False))
        self.assertGreater(bilan["modeles_dbt"], 0)
        self.assertGreater(bilan["tests_sql_dbt"], 0)
        self.assertEqual(bilan["jeux_synthetiques"], 10)

    @unittest.skipIf(yaml is None, "PyYAML absent : contrôles Compose non exécutés")
    def test_compose_services_isoles_ports_loopback_et_init_metabase(self):
        compose = yaml.safe_load((RACINE / "docker-compose.yml").read_text(encoding="utf-8"))
        services = compose["services"]
        for attendu in (
            "postgres-banc",
            "init-banc",
            "airflow-init",
            "airflow-apiserver",
            "airflow-scheduler",
            "airflow-dag-processor",
            "metabase",
            "init-metabase",
            "resultats",
        ):
            self.assertIn(attendu, services)
        self.assertIn("demo", compose["networks"])
        for nom, service in services.items():
            self.assertFalse(service.get("container_name"), nom)
            self.assertFalse(service.get("privileged"), nom)
            self.assertNotEqual(service.get("network_mode"), "host", nom)
            for port in service.get("ports", []):
                self.assertTrue(str(port).startswith("127.0.0.1:"), f"port non loopback : {nom} {port}")
            for volume in service.get("volumes", []):
                source = str(volume).split(":", 1)[0]
                self.assertFalse(source.startswith(("/", "..")), f"montage hôte non permis : {nom} {volume}")
                self.assertNotIn("docker.sock", str(volume), nom)
            reseaux = service.get("networks")
            if reseaux is not None:
                noms = reseaux if isinstance(reseaux, list) else list(reseaux)
                self.assertIn("demo", noms, nom)
        init_mb = services["init-metabase"]
        self.assertEqual(init_mb["restart"], "no")
        self.assertEqual(init_mb["depends_on"]["metabase"]["condition"], "service_healthy")
        self.assertIn("--setup-seul", init_mb["command"])
        for nom in ("airflow-apiserver", "airflow-scheduler", "airflow-dag-processor"):
            self.assertEqual(services[nom]["environment"]["AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION"], "false", nom)

    def test_dag_garde_toutes_les_taches_et_syntaxe_bash(self):
        # Doubles du SDK pour vérifier la construction sans prétendre importer Airflow ici.
        taches = []
        liens = []
        params = {}

        class Param:
            def __init__(self, valeur, **schema):
                self.valeur, self.schema = valeur, schema

        class DAG:
            def __init__(self, **kwargs):
                params.update(kwargs["params"])

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class BashOperator:
            def __init__(self, **kwargs):
                self.task_id = kwargs["task_id"]
                self.bash_command = kwargs["bash_command"]
                taches.append(self)

            def __rshift__(self, autre):
                liens.append((self.task_id, autre.task_id))
                return autre

        noms = [
            "airflow",
            "airflow.providers",
            "airflow.providers.standard",
            "airflow.providers.standard.operators",
            "airflow.providers.standard.operators.bash",
            "airflow.sdk",
        ]
        modules = {n: ModuleType(n) for n in noms}
        modules["airflow.providers.standard.operators.bash"].BashOperator = BashOperator
        modules["airflow.sdk"].DAG = DAG
        modules["airflow.sdk"].Param = Param
        with mock.patch.dict(sys.modules, modules):
            resultat = runpy.run_path(str(RACINE / "airflow/dags/banc_chaine.py"))
        attendues = [
            "preparer",
            "personas",
            "dialogues",
            "controles",
            "juges",
            "charger",
            "accord",
            "rapport",
            "metabase",
            "verifier",
        ]
        self.assertEqual([t.task_id for t in taches], attendues)
        self.assertEqual(liens, list(zip(attendues, attendues[1:])))
        self.assertEqual(params["mode"].schema["enum"], ["simulation"])
        self.assertIn("--simulation /opt/banc/demo/C01", taches[2].bash_command)
        self.assertIn("--personas", taches[2].bash_command)
        self.assertNotIn("source_demo", params)
        if not shutil.which("bash"):
            self.skipTest("bash absent : syntaxe seulement non vérifiée sur ce poste")
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "WINDIR") if k in os.environ}
        env.update({"BANC_MODE": "reel", "BANC_CAMPAGNE": "S01", "PYTHONDONTWRITEBYTECODE": "1"})
        for tache in taches:
            self.assertTrue(tache.bash_command.startswith(resultat["GARDE"]))
            commande = tache.bash_command.replace("/opt/banc-venv/bin/python", sys.executable)
            syntaxe = subprocess.run(
                ["bash", "-n", "-c", commande], cwd=RACINE, env=env, capture_output=True, text=True
            )
            self.assertEqual(syntaxe.returncode, 0, syntaxe.stderr)
            refuse = subprocess.run(["bash", "-c", commande], cwd=RACINE, env=env, capture_output=True, text=True)
            self.assertNotEqual(refuse.returncode, 0)
            self.assertIn("simulation uniquement", refuse.stderr)


if __name__ == "__main__":
    unittest.main()
