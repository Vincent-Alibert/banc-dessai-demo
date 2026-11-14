-- Vue v_accord, reprise telle quelle de sa définition avant dbt (pg_get_viewdef, 16 octobre 2026),
-- seuls les noms de tables sont devenus des références dbt.
SELECT campagne_id,
    version_grille,
    axe_id,
    mesure,
    juges,
    valeur,
    unites,
    motif,
    calcule_le
   FROM {{ source('calcul', 'fait_accord') }}
