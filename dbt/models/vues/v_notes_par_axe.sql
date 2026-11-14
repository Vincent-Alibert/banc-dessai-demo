-- Vue v_notes_par_axe, reprise telle quelle de sa définition avant dbt (pg_get_viewdef, 16 octobre 2026),
-- seuls les noms de tables sont devenus des références dbt.
SELECT r.campagne_id,
    j.nom AS juge,
    j.type_juge,
    j.modele_exact,
    n.passage,
    n.version_grille,
    a.axe_id,
    a.libelle,
    count(n.valeur) AS notes,
    count(*) FILTER (WHERE n.sans_objet) AS sans_objet,
    round(avg(n.valeur), 2) AS moyenne,
    round(stddev_samp(n.valeur), 2) AS ecart_type,
    min(n.valeur) AS minimum,
    max(n.valeur) AS maximum
   FROM {{ ref('fait_note') }} n
     JOIN {{ ref('dim_run') }} r USING (run_id)
     JOIN {{ ref('dim_juge') }} j USING (juge_cle)
     JOIN {{ ref('dim_axe') }} a ON a.axe_id = n.axe_id AND a.version_grille = n.version_grille
  GROUP BY r.campagne_id, j.nom, j.type_juge, j.modele_exact, n.passage, n.version_grille, a.axe_id, a.libelle
