terraform {
  required_providers {
    coder = {
      source = "coder/coder"
    }
    kubernetes = {
      source = "hashicorp/kubernetes"
    }
  }
}

provider "coder" {}
provider "kubernetes" {}

data "coder_workspace" "me" {}
data "coder_workspace_owner" "me" {}

variable "namespace" {
  type    = string
  default = "workspace-plane"
}

variable "workspace_image" {
  type    = string
  default = "registry.lab.internal/sounio/workspace:toolchain-v1"
}

variable "workspace_storage" {
  type    = string
  default = "50Gi"
}

resource "coder_agent" "main" {
  os   = "linux"
  arch = "amd64"
  dir  = "/workspace/src"
  startup_script = <<-EOT
    set -e
    mkdir -p /workspace/src /workspace/home
    if command -v git >/dev/null 2>&1; then
      git config --global --add safe.directory /workspace/src || true
    fi
  EOT
}

resource "kubernetes_persistent_volume_claim" "workspace" {
  metadata {
    name      = "workspace-${data.coder_workspace.me.id}"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/managed-by" = "coder"
      "coder.workspace_id"           = data.coder_workspace.me.id
      "coder.owner"                  = data.coder_workspace_owner.me.name
    }
  }
  spec {
    access_modes       = ["ReadWriteMany"]
    storage_class_name = "proxmox-cephfs"
    resources {
      requests = {
        storage = var.workspace_storage
      }
    }
  }
}

resource "kubernetes_pod" "workspace" {
  metadata {
    name      = "workspace-${data.coder_workspace.me.id}"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/name" = "sounio-workspace"
      "coder.workspace_id"     = data.coder_workspace.me.id
    }
  }
  spec {
    service_account_name = "coder-workspace-provisioner"
    node_selector = {
      "lab.sounio.io/plane" = "workspace"
    }
    security_context {
      fs_group = 1000
    }
    container {
      name              = "workspace"
      image             = var.workspace_image
      image_pull_policy = "IfNotPresent"
      command           = ["sh", "-lc", coder_agent.main.init_script]
      env {
        name  = "HOME"
        value = "/workspace/home"
      }
      env {
        name  = "CODER_AGENT_TOKEN"
        value = coder_agent.main.token
      }
      volume_mount {
        name       = "workspace"
        mount_path = "/workspace"
      }
      resources {
        requests = {
          cpu    = "500m"
          memory = "1Gi"
        }
      }
    }
    volume {
      name = "workspace"
      persistent_volume_claim {
        claim_name = kubernetes_persistent_volume_claim.workspace.metadata[0].name
      }
    }
  }
}
