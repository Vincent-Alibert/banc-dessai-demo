-- Même nomenclature S* que banc/campagnes/campagnes.py.
{% macro campagne_de_test(colonne) -%}
({{ colonne }} ~ '^S(0[1-9]|[1-9][0-9]+)$')
{%- endmacro %}
