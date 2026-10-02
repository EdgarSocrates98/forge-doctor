resource "google_pubsub_topic" "events" {
  name = "events"
}

resource "google_pubsub_subscription" "ok" {
  name  = "sub-ok"
  topic = "events"
  dead_letter_policy {
    dead_letter_topic      = "dlq-events"
    max_delivery_attempts  = 5
  }
}

resource "google_storage_bucket" "b" {
  name          = "b-versioned"
  force_destroy = false
  versioning {
    enabled = true
  }
}

resource "google_dataplex_lake" "lake" {
  name = "lake-prod"
}
