with base as (
    select
        team_id,
        count(*)                                              as total_actions,
        count(*) filter (where event_type = 'Pass')          as total_passes,
        count(*) filter (where event_type = 'Shot')          as total_shots,
        count(*) filter (where event_type = 'Dribble')       as total_dribbles,
        count(*) filter (where event_type = 'Pressure')      as total_pressures,
        count(*) filter (where event_type = 'Carry')         as total_carries,
        round(sum(coalesce(xt_value, 0))::numeric, 4)        as total_xt,
        -- avg() ignores NULLs; coalesce(xp_value, 0) would skew the average down
        round(avg(xp_value)::numeric, 4)                      as avg_xp
    from {{ ref('stg_events') }}
    where team_id is not null
    group by team_id
)

select
    b.team_id,
    dt.team_name,
    b.total_actions,
    b.total_passes,
    b.total_shots,
    b.total_dribbles,
    b.total_pressures,
    b.total_carries,
    b.total_xt,
    b.avg_xp
from base b
left join {{ source('public', 'dim_teams') }} dt on b.team_id = dt.team_id
