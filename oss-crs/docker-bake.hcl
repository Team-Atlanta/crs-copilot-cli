# =============================================================================
# crs-copilot-cli Docker Bake Configuration
# =============================================================================
#
# Builds the CRS base image with Copilot CLI and Python dependencies.
#
# Usage:
#   docker buildx bake prepare
#   docker buildx bake --push prepare   # Push to registry
# =============================================================================

variable "REGISTRY" {
  default = "ghcr.io/team-atlanta"
}

variable "VERSION" {
  default = "cli-0.0.341"
}

variable "COPILOT_CLI_VERSION" {
  default = "0.0.341"
}

function "tags" {
  params = [name]
  result = [
    "${REGISTRY}/${name}:${VERSION}",
    "${name}:${VERSION}"
  ]
}

# -----------------------------------------------------------------------------
# Groups
# -----------------------------------------------------------------------------

group "default" {
  targets = ["prepare"]
}

group "prepare" {
  targets = ["copilot-cli-base"]
}

# -----------------------------------------------------------------------------
# Base Image
# -----------------------------------------------------------------------------

target "copilot-cli-base" {
  context    = "."
  dockerfile = "oss-crs/base.Dockerfile"
  tags       = tags("copilot-cli-base")
  args = {
    COPILOT_CLI_VERSION = COPILOT_CLI_VERSION
  }
}
