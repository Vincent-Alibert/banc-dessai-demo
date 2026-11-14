-- Grille v1.3 : B4 (action) et B5 (confiance) peuvent être « sans objet » seulement si le dialogue s'est
-- arrêté trop tôt pour les observer, soit tour_fin < 3 (même seuil que juger.TOURS_MIN_B4_B5).
-- Une ligne = une note nulle sur B4 ou B5 dans un dialogue d'au moins 3 réponses de Noa.
select n.run_id, n.juge_cle, n.axe_id, n.version_grille, n.passage, r.tour_fin
from {{ ref('fait_note') }} n
join {{ ref('dim_run') }} r using (run_id)
where n.axe_id in ('B4', 'B5') and n.sans_objet and r.tour_fin >= 3
