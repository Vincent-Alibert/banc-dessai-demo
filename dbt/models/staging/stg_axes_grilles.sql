-- Axes de chaque version de grille (une ligne par axe et par version).
select
    a ->> 'axe_id' as axe_id,
    a ->> 'libelle' as libelle,
    a ->> 'definition' as definition,
    coalesce((a ->> 'sans_objet_possible')::boolean, false) as sans_objet_possible,
    g.version_grille
from {{ source('brut', 'grille') }} g,
     jsonb_array_elements(g.axes) a
