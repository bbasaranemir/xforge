{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        analytics_{{ custom_schema_name }}
    {%- endif -%}
{%- endmacro %}
