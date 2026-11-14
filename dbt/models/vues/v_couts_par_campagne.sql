-- Vue v_couts_par_campagne, reprise telle quelle de sa définition avant dbt (pg_get_viewdef, 16 octobre 2026),
-- seuls les noms de tables sont devenus des références dbt.
SELECT campagne_id,
    poste,
    estime,
    source,
    count(DISTINCT run_id) AS runs,
    sum(appels) AS appels,
    sum(jetons_total) AS jetons,
    sum(cout_usd) AS cout_usd,
    count(*) FILTER (WHERE cout_usd IS NULL) AS lignes_sans_prix
   FROM {{ ref('fait_cout') }} c
  GROUP BY campagne_id, poste, estime, source
