-- Axes versionnés (migration 20261012020000) : une ligne par axe ET par version de grille,
-- chacune avec sa propre définition. Échelle de 1 à 4.
select axe_id::text, libelle::text, definition::text,
       1::smallint as echelle_min, 4::smallint as echelle_max,
       sans_objet_possible::boolean, version_grille::text
from {{ ref('stg_axes_grilles') }}
