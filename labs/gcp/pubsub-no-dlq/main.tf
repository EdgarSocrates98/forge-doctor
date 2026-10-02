resource "google_pubsub_topic" "events" {
  name = "events"
}

resource "google_pubsub_subscription" "sub" {
  name  = "sub-events"
  topic = "events"
}
