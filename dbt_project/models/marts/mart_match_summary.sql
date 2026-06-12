-- Per-match scoreline, action volumes, and xT/xG totals.
-- Reads from silver_events (normalised 105×68 coords) and joins fact_events for ML values.

with events as (
    select
        se.event_id,
        se.match_id,
        se.event_type,
        fe.xt_value,
        fe.xg_value
    from {{ ref('silver_events') }} se
    join {{ source('public', 'fact_events') }} fe on se.event_id = fe.event_id
),

event_agg as (
    select
        match_id,
        count(*)                                              as total_events,
        count(*) filter (where event_type = 'Shot')          as total_shots,
        count(*) filter (where event_type = 'Pass')          as total_passes,
        count(*) filter (where event_type = 'Pressure')      as total_pressures,
        round(sum(coalesce(xt_value, 0))::numeric, 4)        as total_xt,
        count(*) filter (where xt_value > 0.05)              as high_xt_actions,
        round(sum(coalesce(xg_value, 0))::numeric, 4)        as total_xg
    from events
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
