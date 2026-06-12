-- Per-player aggregated passing and shooting metrics.
-- Reads from silver_passes and silver_shots (normalised 105×68 coords).
-- Joins fact_events for ML values (xt_value, xg_value, xp_value).

with ml_vals as (
    select event_id, xt_value, xg_value, xp_value
    from {{ source('public', 'fact_events') }}
),

pass_silver as (
    select
        sp.event_id,
        sp.player_id,
        sp.team_id,
        sp.under_pressure,
        sp.pass_distance_m,
        sp.is_successful,
        m.xt_value,
        m.xp_value
    from {{ ref('silver_passes') }} sp
    left join ml_vals m on sp.event_id = m.event_id
    where sp.player_id is not null
),

shot_silver as (
    select
        ss.event_id,
        ss.player_id,
        ss.team_id,
        ss.is_goal,
        m.xt_value,
        m.xg_value
    from {{ ref('silver_shots') }} ss
    left join ml_vals m on ss.event_id = m.event_id
    where ss.player_id is not null
),

all_silver as (
    select se.player_id, se.team_id, m.xt_value
    from {{ ref('silver_events') }} se
    left join ml_vals m on se.event_id = m.event_id
    where se.player_id is not null
),

passes as (
    select
        player_id,
        team_id,
        count(*)                                           as total_passes,
        sum(is_successful::int)                            as successful_passes,
        round(avg(is_successful::int)::numeric * 100, 1)  as pass_completion_pct,
        round(avg(pass_distance_m)::numeric, 2)           as avg_pass_distance_m,
        round(sum(coalesce(xt_value, 0))::numeric, 4)     as total_xt_passes,
        round(
            case when count(*) > 0
                 then sum(coalesce(xt_value, 0)) / count(*)
            end::numeric, 6
        )                                                  as avg_xt_per_pass,
        sum(under_pressure::int)                           as passes_under_pressure,
        round(avg(xp_value)::numeric, 4)                  as avg_xp
    from pass_silver
    group by player_id, team_id
),

shots as (
    select
        player_id,
        team_id,
        count(*)                                           as total_shots,
        sum(is_goal)                                       as goals,
        round(sum(coalesce(xt_value, 0))::numeric, 4)     as total_xt_shots,
        round(sum(coalesce(xg_value, 0))::numeric, 4)     as total_xg,
        round(avg(xg_value)::numeric, 4)                  as avg_xg
    from shot_silver
    group by player_id, team_id
),

all_events as (
    select
        player_id,
        team_id,
        round(sum(coalesce(xt_value, 0))::numeric, 4)     as total_xt
    from all_silver
    group by player_id, team_id
)

select
    coalesce(p.player_id, s.player_id)                    as player_id,
    dp.player_name,
    dt.team_name,
    coalesce(p.total_passes, 0)                           as total_passes,
    coalesce(p.successful_passes, 0)                      as successful_passes,
    p.pass_completion_pct,
    p.avg_pass_distance_m,
    coalesce(p.passes_under_pressure, 0)                  as passes_under_pressure,
    p.avg_xp,
    coalesce(s.total_shots, 0)                            as total_shots,
    coalesce(s.goals, 0)                                  as goals,
    coalesce(s.total_xt_shots, 0)                         as total_xt_shots,
    coalesce(s.total_xg, 0)                               as total_xg,
    s.avg_xg,
    -- finishing_quality: positive = clinical finisher (goals > xG expectation)
    round(
        (coalesce(s.goals, 0) - coalesce(s.total_xg, 0))::numeric, 4
    )                                                      as finishing_quality,
    ae.total_xt,
    p.avg_xt_per_pass
from passes p
full outer join shots s
    on  p.player_id = s.player_id
    and p.team_id   = s.team_id
left join all_events ae on coalesce(p.player_id, s.player_id) = ae.player_id
    and coalesce(p.team_id, s.team_id) = ae.team_id
left join {{ source('public', 'dim_players') }} dp on coalesce(p.player_id, s.player_id) = dp.player_id
left join {{ source('public', 'dim_teams')   }} dt on coalesce(p.team_id,   s.team_id)   = dt.team_id
