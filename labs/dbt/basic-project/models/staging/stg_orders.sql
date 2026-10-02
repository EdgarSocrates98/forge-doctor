{{ config(materialized='view') }}

select
    order_id,
    customer_id,
    ordered_at
from {{ source('raw', 'orders') }}
