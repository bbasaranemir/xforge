with event_agg as (
    select
        match_id,
        count(*)                                              as total_events,
        count(*) filter (where event_type = 'Shot')          as total_shots,
        count(*) filter (where event_type = 'Pass')          as total_passes,
        count(*) filter (where event_type = 'Pressure')      as total_pressures,
        round(sum(coalesce(xt_value, 0))::numeric, 4)        as total_xt,
        count(*) filter (where xt_value > 0.05)              as high_xt_actions,
        -- xg_value is non-NULL only for shots; coalesce to 0 for clean aggregation
        round(sum(coalesce(xg_value, 0))::numeric, 4)        as total_xg
    from {{ ref('stg_events') }}
    group by match_id
)

select
    dm.match_id,
    dm.match_date,
    dm.home_team,
    dm.away_team,
    dm.home_score,
    dm.away_score,
    dm.stage,
    e.total_events,
    e.total_shots,
    e.total_passes,
    e.total_pressures,
    e.total_xt,
    e.high_xt_actions,
    e.total_xg
from {{ source('public', 'dim_matches') }} dm
left join event_agg e on dm.match_id = e.match_id
