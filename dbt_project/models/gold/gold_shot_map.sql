-- Gold: shot-level data ready for Tableau / Metabase scatter plots.
-- Coordinates are already normalised 105×68 from Silver — no further transforms needed.

select
    s.event_id,
    s.match_id,
    s.player_id,
    s.team_id,
    dp.player_name,
    dt.team_name,
    s.location_x       as x_105,
    s.location_y       as y_68,
    s.distance_to_goal,
    s.shot_angle,
    s.is_header,
    s.body_part,
    s.is_goal,
    fe.xg_value,
    s.under_pressure,
    s.minute,
    s.provider
from {{ ref('silver_shots') }} s
left join {{ source('public', 'fact_events') }} fe on s.event_id = fe.event_id
left join {{ source('public', 'dim_players') }} dp on s.player_id = dp.player_id
left join {{ source('public', 'dim_teams') }}   dt on s.team_id   = dt.team_id
