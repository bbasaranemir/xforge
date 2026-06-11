-- Gold: xT threat surface on the universal 105×68 pitch, aggregated to a 16×12 grid.
-- Cell size: 6.5625m × 5.667m (comparable real-world metres, V1 used StatsBomb units).

with passes as (
    select p.*, fe.xt_value
    from {{ ref('silver_passes') }} p
    left join {{ source('public', 'fact_events') }} fe on p.event_id = fe.event_id
    where fe.xt_value is not null
),

gridded as (
    select
        floor(location_x / (105.0 / 16))::int  as grid_col,
        floor(location_y / (68.0  / 12))::int  as grid_row,
        count(*)                                as pass_count,
        round(sum(xt_value)::numeric, 4)        as total_xt,
        round(avg(xt_value)::numeric, 4)        as avg_xt,
        round(max(xt_value)::numeric, 4)        as max_xt
    from passes
    group by grid_col, grid_row
)

select * from gridded
order by total_xt desc
