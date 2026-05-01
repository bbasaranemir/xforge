with passes as (
    select *
    from {{ ref('stg_events') }}
    where event_type = 'Pass'
      and end_location_x is not null
      and end_location_y is not null
)

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
    -- 1 = successful pass (StatsBomb: null outcome = complete)
    case when outcome is null then 1 else 0 end       as is_successful,
    under_pressure,
    xt_value,
    xp_value,
    round(
        sqrt(
            power(end_location_x - location_x, 2) +
            power(end_location_y - location_y, 2)
        )::numeric, 2
    )                                                  as pass_distance
from passes
