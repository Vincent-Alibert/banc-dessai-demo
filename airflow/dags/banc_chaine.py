"""Chaîne source du banc, exclusivement en simulation ; aucun embranchement vers un client réel."""
from __future__ import annotations

from datetime import datetime, timedelta
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

PY = "/opt/banc-venv/bin/python"
GARDE = f'umask 0022; {PY} -m banc.garde --mode "$BANC_MODE" --campagne "$BANC_CAMPAGNE" && '
ENV = {
    "BANC_MODE": "{{ params.mode }}",
    "BANC_CAMPAGNE": "{{ params.campagne }}",
    "BANC_PERSONAS": "{{ params.personas }}",
    "BANC_TOURS": "{{ params.tours }}",
    "BANC_BRAS": "{{ params.bras }}",
    "BANC_PARALLELES": "{{ params.paralleles }}",
    "BANC_ECHELLE": "{{ params.echelle_delais }}",
    "BANC_PLAFOND": "{{ params.plafond_noa_usd }}",
    "BANC_RESERVE": "{{ params.cout_estime_dialogue_usd }}",
}

with DAG(
    dag_id="banc_chaine",
    description="Simulation du vrai lanceur → entrepôt → dbt → accord → rapport → Metabase",
    schedule=None, start_date=datetime(2026, 10, 1), catchup=False, max_active_runs=1,
    tags=["simulation", "synthetique"],
    default_args={"env": ENV, "append_env": True, "cwd": "/opt/banc", "do_xcom_push": False},
    params={
        "mode": Param("simulation", enum=["simulation"]),
        "campagne": Param("S01", type="string", pattern=r"^S(0[1-9]|[1-9][0-9]+)$", maxLength=20),
        "personas": Param("claire,jeanpierre,lucas,mehdi,nadia", type="string",
                          pattern=r"^(claire|jeanpierre|lucas|mehdi|nadia)(,(claire|jeanpierre|lucas|mehdi|nadia))*$",
                          maxLength=100),
        "tours": Param(5, type="integer", minimum=1, maximum=5),
        "bras": Param("les_deux", enum=["sans_onboarding", "avec_onboarding", "les_deux"]),
        "paralleles": Param(4, type="integer", minimum=1, maximum=4),
        "echelle_delais": Param(0, type="number", minimum=0, maximum=10),
        "plafond_noa_usd": Param(1, type="number", minimum=0),
        "cout_estime_dialogue_usd": Param(0.04, type="number", minimum=0),
    },
) as dag:
    preparer = BashOperator(task_id="preparer", bash_command=GARDE +
        f'{PY} -m entrepot.preparer --mode "$BANC_MODE" --campagne "$BANC_CAMPAGNE" --personas "$BANC_PERSONAS"')
    personas = BashOperator(task_id="personas", bash_command=GARDE +
        f'{PY} -m entrepot.verifier_personas --personas "$BANC_PERSONAS"')
    dialogues = BashOperator(task_id="dialogues", bash_command=GARDE +
        f'{PY} -m banc.dialogues.dialoguer --mode simulation --campagne "$BANC_CAMPAGNE" '
        '--simulation /opt/banc/demo/C01 --tours "$BANC_TOURS" --bras "$BANC_BRAS" '
        '--paralleles "$BANC_PARALLELES" --echelle-delais "$BANC_ECHELLE" '
        '--plafond-usd "$BANC_PLAFOND" --cout-estime-dialogue "$BANC_RESERVE" --version-persona v1 '
        '--reprendre --personas "$BANC_PERSONAS"',
        retries=1, retry_delay=timedelta(seconds=10), execution_timeout=timedelta(hours=3))
    controles = BashOperator(task_id="controles", bash_command=GARDE +
        f'{PY} -m banc.personas.controles_persona --campagne "$BANC_CAMPAGNE"')
    juges = BashOperator(task_id="juges", bash_command=GARDE +
        f'{PY} -m banc.juges.exporter_notation --campagne "$BANC_CAMPAGNE" && '
        f'{PY} -m banc.juges.juger --campagne "$BANC_CAMPAGNE" --juges factice,factice-b --paralleles "$BANC_PARALLELES"',
        retries=1, retry_delay=timedelta(seconds=10))
    charger = BashOperator(task_id="charger", bash_command=GARDE +
        f'{PY} -m entrepot.charger --campagne "$BANC_CAMPAGNE"')
    accord = BashOperator(task_id="accord", bash_command=GARDE +
        f'{PY} -m entrepot.calculer_accord --campagne "$BANC_CAMPAGNE" --inclure-factices')
    rapport = BashOperator(task_id="rapport", bash_command=GARDE +
        f'{PY} -m entrepot.rapport --campagne "$BANC_CAMPAGNE"')
    metabase = BashOperator(task_id="metabase", bash_command=GARDE +
        f'{PY} -m infra.metabase_configurer --mode simulation --campagne "$BANC_CAMPAGNE"')
    verifier = BashOperator(task_id="verifier", bash_command=GARDE +
        f'{PY} -m infra.verifier_resultats --campagne "$BANC_CAMPAGNE"')
    preparer >> personas >> dialogues >> controles >> juges >> charger >> accord >> rapport >> metabase >> verifier
