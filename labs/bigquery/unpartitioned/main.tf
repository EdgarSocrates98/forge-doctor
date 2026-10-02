resource "google_bigquery_dataset" "analytics" {
  dataset_id = "analytics"
  location   = "US"
}

resource "google_bigquery_table" "events" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  table_id   = "events"

  time_partitioning {
    type  = "DAY"
    field = "event_date"
  }
}

resource "google_bigquery_table" "archive" {
  dataset_id = google_bigquery_dataset.analytics.dataset_id
  table_id   = "archive"
}
