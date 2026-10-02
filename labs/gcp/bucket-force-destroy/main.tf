resource "google_storage_bucket" "raw" {
  name          = "raw-tmp"
  force_destroy = true
}
