-- Vue v_incidents, reprise telle quelle de sa définition avant dbt (pg_get_viewdef, 16 octobre 2026),
-- seuls les noms de tables sont devenus des références dbt.
SELECT campagne_id,
    type,
    gravite,
    origine,
    count(*) AS nombre,
    count(DISTINCT run_id) AS runs
   FROM {{ ref('fait_incident') }}
  GROUP BY campagne_id, type, gravite, origine
