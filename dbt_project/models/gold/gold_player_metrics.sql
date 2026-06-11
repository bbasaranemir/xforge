-- Gold: per-player, per-match summary combining pass and shot metrics.
-- Joins xT (written by xt_model.py) and xG (written by xg_model.py) from fact_events.

with passes as (
    select * from {{ ref('silver_passes') }}
),

shots as (
    select * from {{ ref('silver_shots') }}
),

fe as (
    select event_id, xt_value, xg_value
    from {{ source('public', 'fact_events') }}
),

players as (select * from {{ source('public', 'dim_players') }}),
teams   as (select * from {{ source('public', 'dim_teams')   }}),

pass_agg as (
    select
        p.player_id,
        p.team_id,
        p.match_id,
        count(*)                                                as total_passes,
        sum(case when p.is_successful then 1 else 0 end)       as successful_passes,
        round(avg(p.pass_distance_m)::numeric, 2)              as avg_pass_distance_m,
        round(sum(coalesce(fe.xt_value, 0))::numeric, 4)       as total_xt,
        sum(case when p.under_pressure then 1 else 0 end)      as passes_under_pressure
    from passes p
    left join fe on p.event_id = fe.event_id
    group by p.player_id, p.team_id, p.match_id
),

shot_agg as (
    select
        s.player_id,
        s.team_id,
        s.match_id,
        count(*)                                                as total_shots,
        round(sum(coalesce(fe.xg_value, 0))::numeric, 4)       as total_xg,
        sum(s.is_goal)                                          as goals
    from shots s
    left join fe on s.event_id = fe.event_id
    group by s.player_id, s.team_id, s.match_id
)

select
    coalesce(pa.player_id, sa.player_id)        as player_id,
    pl.player_name,
    t.team_name,
    coalesce(pa.match_id,  sa.match_id)         as match_id,
    coalesce(pa.total_passes, 0)                as total_passes,
    coalesce(pa.successful_passes, 0)           as successful_passes,
    round(
        coalesce(pa.successful_passes, 0) * 100.0
        / nullif(coalesce(pa.total_passes, 0), 0)
    ::numeric, 2)                               as pass_completion_pct,
    pa.avg_pass_distance_m,
    coalesce(pa.total_xt, 0)                    as total_xt,
    coalesce(pa.passes_under_pressure, 0)       as passes_under_pressure,
    coalesce(sa.total_shots, 0)                 as total_shots,
    coalesce(sa.total_xg, 0)                    as total_xg,
    coalesce(sa.goals, 0)                       as goals
from pass_agg pa
full outer join shot_agg sa
    on  pa.player_id = sa.player_id
    and pa.match_id  = sa.match_id
left join players pl on coalesce(pa.player_id, sa.player_id) = pl.player_id
left join teams   t  on coalesce(pa.team_id,   sa.team_id)   = t.team_id
