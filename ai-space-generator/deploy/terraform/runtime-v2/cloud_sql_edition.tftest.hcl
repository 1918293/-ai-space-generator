mock_provider "google" {}
mock_provider "google-beta" {}

variables {
  project_id                  = "hao-runtime-v2-test"
  region                      = "asia-east1"
  cloud_sql_edition           = "ENTERPRISE"
  cloud_sql_tier              = "db-custom-1-3840"
  cloud_sql_availability_type = "ZONAL"
}

run "enterprise_custom_tier_allowed" {
  command = plan
}

run "enterprise_plus_rejects_enterprise_custom_tier" {
  command = plan
  variables { cloud_sql_edition = "ENTERPRISE_PLUS" }
  expect_failures = [google_sql_database_instance.runtime]
}
