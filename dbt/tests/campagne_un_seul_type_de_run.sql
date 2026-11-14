-- Une campagne contient des dialogues OU des tests à entrée fixe, jamais les deux (les lanceurs le
-- refusent) ; et, pour les dialogues, une seule version du persona (banc/dialogues/dialoguer.py le refuse).
select campagne_id, string_agg(distinct type_run, ',') as types, string_agg(distinct version_persona, ',') as versions
from {{ ref('dim_run') }}
group by campagne_id
having count(distinct type_run) > 1 or count(distinct version_persona) > 1
