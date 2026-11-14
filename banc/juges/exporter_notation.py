"""
Export anonymise des dialogues d'une campagne, pour la notation croisee.

Pour chaque run note (les runs marques « hors notation » sont exclus), un
fichier runs/<campagne>/notation/dialogues/<id>.json qui ne contient QUE :
l'identifiant opaque, et les tours (message de l'utilisateur, reponse
nettoyee de Noa) ; pour un test a entree fixe (type_run « test_fixe »), aussi son contexte fixe
(echange anterieur ecrit a la main, identique pour toutes les versions de Noa, non note). Pas d'axes, pas de fiche, pas de cout, pas de bras, pas de
nom de persona dans les cles. Le juge ne doit pas etre influence.

La correspondance identifiant -> run vit dans un fichier separe,
runs/<campagne>/notation/correspondance.json, que les juges ne recoivent pas.

Les identifiants sont melanges avec une graine fixe : l'ordre des fichiers ne
dit rien de l'ordre des personas ni des bras.

Usage :  python -m banc.juges.exporter_notation --campagne C01
"""

from __future__ import annotations

import json
import random
import sys

from banc import chemins
from banc.garde import campagne_valide, exiger_simulation

RUNS = chemins.RUNS
GRAINE = 20261009


def main() -> int:
    args = sys.argv[1:]
    if "--campagne" not in args:
        sys.exit("[X] --campagne CXX obligatoire")
    exiger_simulation()
    campagne = campagne_valide(args[args.index("--campagne") + 1])
    dossier = RUNS / campagne
    fichiers = sorted(p for p in dossier.glob(f"{campagne}-*.json"))
    if not fichiers:
        sys.exit(f"[X] aucun run dans {dossier}")

    retenus, exclus = [], []
    for f in fichiers:
        r = json.loads(f.read_text(encoding="utf-8"))
        marquage = r.get("marquage") or ""
        if "hors notation" in marquage:
            exclus.append((f.name, marquage))
            continue
        if not any(t.get("noa") for t in r["tours"]):
            exclus.append((f.name, "aucun tour joue"))
            continue
        retenus.append((f, r))

    # Codes stables : un run deja code garde son code d'un export a l'autre (sinon des notes deja rendues
    # suivraient un autre run) ; seuls les nouveaux runs recoivent des codes neufs, melanges avec la graine.
    fichier_corr = dossier / "notation" / "correspondance.json"
    deja = {}
    if fichier_corr.exists():
        for code, v in json.loads(fichier_corr.read_text(encoding="utf-8"))["dialogues"].items():
            deja[v["fichier"]] = code
    pris = {deja[f.name] for f, _ in retenus if f.name in deja}
    nouveaux = [f.name for f, _ in retenus if f.name not in deja]
    libres = [f"N{i:02d}" for i in range(1, len(retenus) + len(deja) + 1) if f"N{i:02d}" not in set(deja.values())]
    libres = libres[:len(nouveaux)]
    rnd = random.Random(GRAINE)
    rnd.shuffle(libres)
    attribution = dict(zip(nouveaux, libres))
    ids = [deja.get(f.name) or attribution[f.name] for f, _ in retenus]
    assert len(set(ids)) == len(ids) and not (pris & set(attribution.values()))

    sortie = dossier / "notation" / "dialogues"
    sortie.mkdir(parents=True, exist_ok=True)
    for ancien in sortie.glob("*.json"):
        ancien.unlink()
    correspondance = {}
    for ident, (f, r) in zip(ids, retenus):
        tours = []
        for t in r["tours"]:
            if t.get("persona") is None:
                continue
            tours.append({"n": t["n"], "utilisateur": t["persona"], "noa": t.get("noa") if t.get("noa") is not None else ""})
        contexte = (r.get("test_fixe") or {}).get("contexte") or []
        (sortie / f"{ident}.json").write_text(
            json.dumps({"id": ident, **({"contexte": contexte} if contexte else {}), "tours": tours},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        correspondance[ident] = {"fichier": f.name, "persona": r["persona"]["identifiant"], "bras": r.get("bras"),
                                 "ouverture": r.get("ouverture"), "marquage": r.get("marquage"),
                                 "tours": len(tours), "type_run": r.get("type_run") or "dialogue",
                                 "version_noa": r.get("version_noa"),
                                 "test_fixe": (r.get("test_fixe") or {}).get("id")}
    (dossier / "notation" / "correspondance.json").write_text(
        json.dumps({"campagne": campagne, "graine": GRAINE, "dialogues": correspondance}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print(f"{len(retenus)} dialogue(s) exporte(s) dans {sortie}")
    for ident in sorted(correspondance):
        print(f"  {ident}  {correspondance[ident]['tours']} tours")
    for nom, raison in exclus:
        print(f"  exclu : {nom} — {raison}")
    print(f"correspondance : {dossier / 'notation' / 'correspondance.json'}  (ne pas montrer aux juges)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
