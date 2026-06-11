-- Bronze layer: pass-through with explicit type casts and a row lineage tag.
-- No coordinate changes. No business logic.
-- Downstream Silver models read from here, never from the raw public schema.

with source as (
    select * from {{ source('public', 'fact_events') }}
),

bronze as (
    select
        event_id::uuid                  as event_id,
        match_id::integer               as match_id,
        team_id::integer                as team_id,
        player_id::integer              as player_id,
        event_type::varchar             as event_type,
        minute::integer                 as minute,
        second::integer                 as second,
        location_x::float               as location_x,
        location_y::float               as location_y,
        end_location_x::float           as end_location_x,
        end_location_y::float           as end_location_y,
        outcome::varchar                as outcome,
        under_pressure::boolean         as under_pressure,
        provider::varchar               as provider,
        coord_system::varchar           as coord_system,
        raw_json::jsonb                 as raw_json,
        coalesce(ingested_at, now())    as ingested_at
    from source
    where event_id is not null
)

select * from bronze
