select
    event_id,
    match_id,
    competition_id,
    team_id,
    player_id,
    minute,
    second,
    location_x,
    location_y,
    end_location_x,
    end_location_y,
    outcome,
    under_pressure,
    xt_value,
    xg_value,
    case when outcome = 'Goal' then 1 else 0 end as is_goal
from {{ ref('stg_events') }}
where event_type = 'Shot'
