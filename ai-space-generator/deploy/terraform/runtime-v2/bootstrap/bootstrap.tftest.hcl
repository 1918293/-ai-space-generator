mock_provider "google" {}

variables {
  project_id        = "hao-runtime-v2-bootstrap-test"
  state_bucket_name = "hao-runtime-v2-bootstrap-test-state"
}

run "bootstrap_contract_is_fail_closed" {
  command = plan

  assert {
    condition     = google_storage_bucket.terraform_state.force_destroy == false
    error_message = "Terraform state bucket must not allow force_destroy."
  }

  assert {
    condition     = google_storage_bucket.terraform_state.public_access_prevention == "enforced"
    error_message = "Terraform state bucket must enforce public access prevention."
  }

  assert {
    condition     = google_storage_bucket.terraform_state.uniform_bucket_level_access == true
    error_message = "Terraform state bucket must use uniform bucket-level access."
  }

  assert {
    condition     = google_storage_bucket.terraform_state.versioning[0].enabled == true
    error_message = "Terraform state bucket must keep object versioning enabled."
  }

  assert {
    condition = (
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "repository_id == '1304812158'") &&
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "repository_owner_id == '133175353'") &&
      strcontains(google_iam_workload_identity_pool_provider.github.attribute_condition, "refs/heads/exp/execution-control-plane-v1")
    )
    error_message = "WIF provider must remain restricted to the immutable repository identity and shared EXP branch."
  }

  assert {
    condition = (
      !contains(local.deployment_project_roles, "roles/owner") &&
      !contains(local.deployment_project_roles, "roles/editor")
    )
    error_message = "Bootstrap must never grant primitive Owner or Editor roles to the Terraform deployer."
  }
}
