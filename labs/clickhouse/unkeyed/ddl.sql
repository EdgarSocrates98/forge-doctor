CREATE TABLE events (id UInt64, ts DateTime) ENGINE = MergeTree() PARTITION BY toYYYYMM(ts);

CREATE TABLE rep (id UInt64) ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/rep', '{replica}') ORDER BY id;

CREATE TABLE dist AS rep ENGINE = Distributed('prod_cluster', 'default', 'missing_local');

CREATE TABLE kafka_queue (payload String) ENGINE = Kafka SETTINGS kafka_broker_list='broker:9092', kafka_topic_list='events';
