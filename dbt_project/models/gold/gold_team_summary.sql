-- Gold: team-level match summary with xT, xG, and conversion rate.
-- Powers the mv_team_xg materialized view and BI dashboards.

with events as (select * from {{ ref('silver_events') }}),
fe    as (select event_id, xt_value, xg_value, outcome from {{ source('public', 'fact_events') }}),
teams as (select * from {{ source('public', 'dim_teams') }}),

agg as (
    select
        e.team_id,
        e.match_id,
        count(*)                                                as total_actions,
        count(*) filter (where e.event_type = 'Pass')          as total_passes,
        count(*) filter (where e.event_type = 'Shot')          as total_shots,
        count(*) filter (where fe.outcome = 'Goal')            as goals,
        round(sum(coalesce(fe.xg_value, 0))::numeric, 4)       as total_xg,
        round(sum(coalesce(fe.xt_value, 0))::numeric, 4)       as total_xt,
        round(
            count(*) filter (where fe.outcome = 'Goal') * 1.0
            / nullif(count(*) filter (where e.event_type = 'Shot'), 0)
        ::numeric, 4)                                          as conversion_rate
    from events e
    left join fe on e.event_id = fe.event_id
    where e.team_id is not null
    group by e.team_id, e.match_id
)

select a.*, t.team_name
from agg a
left join teams t on a.team_id = t.team_id
