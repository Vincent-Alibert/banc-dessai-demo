-- Vue v_runs, reprise telle quelle de sa définition avant dbt (pg_get_viewdef, 16 octobre 2026),
-- seuls les noms de tables sont devenus des références dbt.
SELECT r.campagne_id,
    r.run_id,
    r.code_notation,
    p.identifiant AS persona,
    p.empreinte_fiche,
    r.bras,
    r.ouverture,
    r.statut,
    r.tours_demandes,
    r.tours_joues,
    r.duree_totale_s,
    ( SELECT sum(c.cout_usd) AS sum
           FROM {{ ref('fait_cout') }} c
          WHERE c.run_id = r.run_id AND c.poste ~~ 'noa_%'::text AND NOT c.estime) AS cout_noa_usd,
    ( SELECT count(*) AS count
           FROM {{ ref('fait_incident') }} i
          WHERE i.run_id = r.run_id AND (i.gravite = ANY (ARRAY['majeur'::text, 'bloquant'::text]))) AS incidents_graves,
    -- 16 octobre 2026 : fin du dialogue, versions, type de run, jeu de fiches (colonnes ajoutées en fin)
    r.type_run,
    r.version_noa,
    r.version_persona,
    p.jeu,
    r.statut_fin,
    r.statut_fin_derive,
    r.motif_fin,
    r.tour_fin,
    r.cout_simule_usd,
    r.mode_execution
   FROM {{ ref('dim_run') }} r
     JOIN {{ ref('dim_persona') }} p USING (persona_cle)
