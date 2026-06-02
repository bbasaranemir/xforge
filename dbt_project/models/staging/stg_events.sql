with source as (
    select
        event_id,
        match_id,
        competition_id,
        team_id,
        player_id,
        event_type,
        minute,
        second,
        location_x,
        location_y,
        end_location_x,
        end_location_y,
        outcome,
        under_pressure,
        xt_value,
        xp_value,
        xg_value
    from {{ source('public', 'fact_events') }}
    where location_x is not null
      and location_y is not null
      and event_type is not null
)

select
    event_id::text                         as event_id,
    match_id::integer                      as match_id,
    competition_id::integer                as competition_id,
    team_id::integer                       as team_id,
    player_id::integer                     as player_id,
    event_type,
    minute::smallint                       as minute,
    coalesce(second, 0)::smallint          as second,
    round(location_x::numeric, 2)         as location_x,
    round(location_y::numeric, 2)         as location_y,
    round(end_location_x::numeric, 2)     as end_location_x,
    round(end_location_y::numeric, 2)     as end_location_y,
    outcome,
    coalesce(under_pressure, false)        as under_pressure,
    xt_value,
    xp_value,
    xg_value
from source
