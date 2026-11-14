-- Vue v_notes_pour_accord, reprise telle quelle de sa définition avant dbt (pg_get_viewdef, 16 octobre 2026),
-- seuls les noms de tables sont devenus des références dbt.
SELECT r.campagne_id,
    n.run_id,
    r.code_notation,
    n.axe_id,
    n.version_grille,
    j.nom AS juge,
    n.passage,
    n.valeur
   FROM {{ ref('fait_note') }} n
     JOIN {{ ref('dim_run') }} r USING (run_id)
     JOIN {{ ref('dim_juge') }} j USING (juge_cle)
  WHERE j.type_juge = 'factice'::text AND (r.statut = ANY (ARRAY['complet'::text, 'interrompu_produit'::text]))
