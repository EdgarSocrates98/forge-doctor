{{ config(materialized='incremental') }}

select
    order_id,
    customer_id,
    ordered_at
from {{ ref('stg_orders') }}
{% if is_incremental() %}
where ordered_at > (select max(ordered_at) from {{ this }})
{% endif %}
