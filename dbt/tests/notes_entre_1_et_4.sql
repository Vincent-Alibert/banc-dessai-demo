-- Une note vaut 1 à 4, ou elle est nulle quand l'axe est sans objet ; « sans objet » n'est permis
-- que sur un axe qui le prévoit (B6 Honnêteté) ; le passage commence à 1.
select n.run_id, n.juge_cle, n.axe_id, n.version_grille, n.passage, n.valeur, n.sans_objet
from {{ ref('fait_note') }} n
join {{ ref('dim_axe') }} a using (axe_id, version_grille)
where (n.valeur is not null and n.valeur not between a.echelle_min and a.echelle_max)
   or ((n.valeur is null) <> n.sans_objet)
   or (n.sans_objet and not a.sans_objet_possible)
   or n.passage < 1
