"""Contrôles statiques reproductibles ; ils ne remplacent pas une exécution Docker/Postgres/dbt."""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

RACINE = Path(__file__).resolve().parents[1]
INTERDITS = {"openai", "google.genai", "banc.dialogues.client_noa", "banc.dialogues.comptes",
             "banc.personas.moteur_persona", "banc.base", "banc.juges.appels_api"}


def fichiers_code(racine):
    return sorted(p for p in racine.rglob("*") if p.is_file()
                  and not any(x in {"__pycache__", ".travail", "runs", "target", "logs"} for x in p.relative_to(racine).parts))


def verifier(racine=RACINE):
    erreurs = []
    fichiers = fichiers_code(racine)
    python = [p for p in fichiers if p.suffix == ".py"]
    for p in python:
        texte = p.read_text(encoding="utf-8")
        try:
            compile(texte, str(p), "exec")
            arbre = ast.parse(texte)
        except SyntaxError as exc:
            erreurs.append(str(p.relative_to(racine)) + " : " + str(exc))
            continue
        for n in ast.walk(arbre):
            modules = ([n.module] if isinstance(n, ast.ImportFrom) else [a.name for a in n.names]
                       if isinstance(n, ast.Import) else [])
            for module in modules:
                if not module or module == "__future__":
                    continue
                if any(module == nom or module.startswith(nom + ".") for nom in INTERDITS):
                    erreurs.append(f"import réel interdit : {p.relative_to(racine)} → {module}")
                if module.split(".")[0] in {"banc", "entrepot", "infra"}:
                    cible = racine.joinpath(*module.split("."))
                    if not cible.with_suffix(".py").is_file() and not (cible / "__init__.py").is_file():
                        erreurs.append("import local manquant : " + module)
    modeles = {p.stem: p for p in (racine / "dbt/models").rglob("*.sql")}
    seeds = {p.stem for p in (racine / "dbt/seeds").glob("*.csv")}
    noms = [p.stem for p in (racine / "dbt/models").rglob("*.sql")]
    if len(noms) != len(set(noms)):
        erreurs.append("noms de modèles dbt en double")
    references = []
    sources = []
    for p in [*(racine / "dbt").rglob("*.sql"), *(racine / "dbt").rglob("*.yml")]:
        texte = p.read_text(encoding="utf-8")
        for ref in re.findall(r"\bref\(['\"]([^'\"]+)['\"]\)", texte):
            references.append(ref)
            if ref not in modeles and ref not in seeds:
                erreurs.append(f"référence dbt absente : {p.relative_to(racine)} → {ref}")
        sources += re.findall(r"source\(['\"]([^'\"]+)['\"],\s*['\"]([^'\"]+)['\"]\)", texte)
    graphe = {nom: set(re.findall(r"\bref\(['\"]([^'\"]+)['\"]\)", p.read_text())) & set(modeles)
              for nom, p in modeles.items()}
    reste = dict(graphe)
    while reste:
        libres = {n for n, deps in reste.items() if not deps.intersection(reste)}
        if not libres:
            erreurs.append("cycle dans le graphe dbt : " + ",".join(sorted(reste)))
            break
        reste = {n: deps for n, deps in reste.items() if n not in libres}
    for p in (racine / "demo/C01").glob("C01-*.json"):
        run = json.loads(p.read_text(encoding="utf-8"))
        if run.get("provenance", {}).get("nature") != "jeu_synthetique":
            erreurs.append("provenance synthétique absente : " + p.name)
        if any(k in run for k in ("compte", "requete", "contexte_utilisateur")):
            erreurs.append("données de compte inattendues dans le jeu : " + p.name)
    empreintes = json.loads((racine / "demo/C01/empreintes.json").read_text(encoding="utf-8"))
    for nom, sha in empreintes.items():
        p = racine / "demo/C01" / nom
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            erreurs.append("empreinte synthétique invalide : " + nom)
    motifs = [re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"), re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}"),
              re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")]
    for p in fichiers:
        if p.suffix not in {".py", ".json", ".md", ".yml", ".sql", ".txt", ".csv", ".sh", ".ps1"}:
            continue
        texte = p.read_text(encoding="utf-8")
        if any(m.search(texte) for m in motifs):
            erreurs.append("motif de secret à examiner : " + str(p.relative_to(racine)))
    yaml_statut = "non exécuté : PyYAML absent"
    jinja_statut = "non exécuté : Jinja2 absent"
    services = []
    if importlib.util.find_spec("yaml"):
        import yaml
        for p in [*(racine / "dbt").rglob("*.yml"), racine / "docker-compose.yml"]:
            yaml.safe_load(p.read_text(encoding="utf-8"))
        compose = yaml.safe_load((racine / "docker-compose.yml").read_text(encoding="utf-8"))
        services = list(compose["services"])
        for nom, service in compose["services"].items():
            if service.get("container_name") or service.get("privileged") or service.get("network_mode") == "host":
                erreurs.append("service non isolé : " + nom)
            for port in service.get("ports", []):
                if not str(port).startswith("127.0.0.1:"):
                    erreurs.append("port non loopback : " + nom)
            for volume in service.get("volumes", []):
                source = volume.split(":", 1)[0]
                if source.startswith(("/", "..")) or "docker.sock" in volume:
                    erreurs.append("montage hôte non permis : " + nom)
            reseaux = service.get("networks")
            if reseaux is not None:
                noms = reseaux if isinstance(reseaux, list) else list(reseaux)
                if "demo" not in noms:
                    erreurs.append("service hors du réseau de démo : " + nom)
        if "demo" not in compose.get("networks", {}):
            erreurs.append("réseau de démo « demo » absent")
        for v in compose["volumes"].values():
            if v and (v.get("external") or v.get("name")):
                erreurs.append("volume partagé ou nommé hors projet")
        declaration = yaml.safe_load((racine / "dbt/models/sources.yml").read_text())
        tables = {(s["name"], t["name"]): s["schema"] + "." + t["name"]
                  for s in declaration["sources"] for t in s["tables"]}
        for source in sources:
            if source not in tables:
                erreurs.append("source dbt non déclarée : " + str(source))
        ddl = "\n".join(p.read_text() for p in (racine / "entrepot/migrations").glob("*.sql"))
        creees = set(re.findall(r"CREATE TABLE\s+([\w.]+)", ddl, re.I))
        for table in tables.values():
            if table not in creees:
                erreurs.append("table brute/calcul sans migration : " + table)
        qualite = yaml.safe_load((racine / "dbt/models/etoile/_etoile.yml").read_text())
        for model in qualite["models"]:
            if model["name"] not in modeles:
                erreurs.append("tests YAML sur modèle supprimé : " + model["name"])
        yaml_statut = "réussi : syntaxe YAML, sources/migrations, montages, volumes et ports"
        if importlib.util.find_spec("jinja2"):
            from jinja2 import Environment, StrictUndefined
            env = Environment(undefined=StrictUndefined)  # nosec B701 # rendu du SQL des modeles dbt, jamais de HTML
            macros = env.from_string((racine / "dbt/macros/campagnes.sql").read_text()).module
            contexte = {"ref": lambda n: ("banc_staging." if n.startswith("stg_") or n in seeds else "banc.") + n,
                        "source": lambda a, b: tables[(a, b)], "campagne_de_test": macros.campagne_de_test}
            for p in [*(racine / "dbt/models").rglob("*.sql"), *(racine / "dbt/tests").glob("*.sql")]:
                rendu = env.from_string(p.read_text()).render(**contexte)
                if "{{" in rendu or "{%" in rendu:
                    erreurs.append("Jinja non résolu : " + p.name)
            jinja_statut = "réussi : rendu Jinja des modèles/tests ; pas de compilation SQL Postgres"
    return {"python_compiles": len(python), "modeles_dbt": len(modeles), "tests_sql_dbt": len(list((racine / "dbt/tests").glob("*.sql"))),
            "references_dbt": len(references), "jeux_synthetiques": len(empreintes), "services": services,
            "yaml": yaml_statut, "jinja": jinja_statut, "erreurs": erreurs}


if __name__ == "__main__":
    bilan = verifier()
    print(json.dumps(bilan, ensure_ascii=False, indent=2))
    sys.exit(bool(bilan["erreurs"]))
