-- Runs de dialogue : ceux qui portent un persona (les spikes de C00 restent dans la couche brute).
select
    b.fichier,
    regexp_replace(regexp_replace(b.fichier, '^.*/', ''), '\.json$', '') as run_id,
    b.campagne,
    b.empreinte,
    b.contenu as c,
    -- empreinte de la fiche complète si le run la porte (depuis le 11 octobre 2026), sinon
    -- empreinte du résumé de persona enregistré dans le run (C00, C01)
    coalesce(b.contenu -> 'persona' ->> 'empreinte_fiche',
             left(md5((b.contenu -> 'persona')::text), 16)) as empreinte_persona,
    -- horodatage du run, toujours en UTC (indépendant du fuseau de la session)
    regexp_replace(b.contenu ->> 'horodatage_utc',
                   '^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$', '\1-\2-\3 \4:\5:\6+00')::timestamptz as horodatage
from {{ source('brut', 'run') }} b
where b.contenu ? 'persona'
