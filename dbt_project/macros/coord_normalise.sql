-- Universal coordinate normalisation macros.
-- All Silver models call these instead of inlining the CASE logic.
-- To add a new provider, add one WHEN branch here — zero changes to Silver models.

{% macro normalise_x(x_col, coord_system_col) %}
CASE {{ coord_system_col }}
    WHEN 'statsbomb_120x80' THEN {{ x_col }} * (105.0 / 120.0)
    WHEN 'opta_100x100'     THEN {{ x_col }} * (105.0 / 100.0)
    WHEN 'wyscout_100x100'  THEN {{ x_col }} * (105.0 / 100.0)
    ELSE {{ x_col }}
END
{% endmacro %}

{% macro normalise_y(y_col, coord_system_col) %}
CASE {{ coord_system_col }}
    WHEN 'statsbomb_120x80' THEN {{ y_col }} * (68.0 / 80.0)
    WHEN 'opta_100x100'     THEN {{ y_col }} * (68.0 / 100.0)
    WHEN 'wyscout_100x100'  THEN {{ y_col }} * (68.0 / 100.0)
    ELSE {{ y_col }}
END
{% endmacro %}
