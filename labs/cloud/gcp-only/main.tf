resource "google_storage_bucket" "raw" {
  name     = "raw-bucket"
  location = "EU"
}

resource "google_pubsub_topic" "events" {
  name = "events"
}

resource "google_bigquery_dataset" "dw" {
  dataset_id = "dw"
}

resource "google_dataproc_cluster" "spark" {
  name   = "spark-cluster"
  region = "europe-west1"
}
