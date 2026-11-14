-- Contrôles du persona (banc/personas/controles_persona.py) : une ligne par run contrôlé, le fichier de contrôle le
-- plus récent de chaque campagne (un seul fichier par campagne, réécrit à chaque passage).
select
    b.campagne,
    regexp_replace(x ->> 'fichier_run', '\.json$', '') as run_id,
    b.contenu ->> 'version_controles' as version_controles,
    (b.contenu ->> 'horodatage_utc')::timestamptz as controle_le,
    x as c
from {{ source('brut', 'controle_persona') }} b,
     jsonb_array_elements(coalesce(b.contenu -> 'runs', '[]')) x
