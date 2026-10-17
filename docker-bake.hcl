# docker-bake.hcl
# Builds the release images that `make up-prod-pull` pulls from GHCR.
# Used by .github/workflows/images.yml; locally: `VERSION=$(cat VERSION) docker buildx bake --print`.

variable "VERSION" {
  default = "latest"
}

variable "REGISTRY" {
  default = "ghcr.io/seg-unibe/bf-cbom"
}

# Python-only images run natively on Apple Silicon too
variable "MULTI_ARCH" {
  default = ["linux/amd64", "linux/arm64"]
}

group "default" {
  targets = [
    "coordinator",
    "worker-cbomkit",
    "worker-cdxgen",
    "worker-cryptobomforge",
    "worker-deepseek",
    "worker-chatgpt",
    "worker-claude",
    "worker-cryptoscan",
    "worker-pqcheck",
    "worker-qdarpan",
    "worker-securin-xbom",
    "worker-cipherradar",
    "worker-xgrep",
    "worker-cbomkit-jar",
    "worker-ciphersift",
    "worker-scanoss",
    "misc-pyqun",
    "gitserver",
    "runner",
  ]
}

# Shared base; consumed via a named build context, never pushed
target "base" {
  dockerfile = "docker/Dockerfile.base"
  platforms  = MULTI_ARCH
}

# Links each GHCR package to the repo
target "_common" {
  labels = {
    "org.opencontainers.image.source" = "https://github.com/SEG-UNIBE/BF-CBOM"
  }
}

target "_from-base" {
  inherits = ["_common"]
  contexts = {
    "bf-cbom/base:${VERSION}" = "target:base"
  }
  args = {
    BASE_IMAGE = "bf-cbom/base"
    BASE_TAG   = VERSION
  }
  platforms = MULTI_ARCH
}

target "coordinator" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.coordinator"
  tags       = ["${REGISTRY}-coordinator:${VERSION}"]
}

target "runner" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.runner"
  tags       = ["${REGISTRY}-runner:${VERSION}"]
}

target "gitserver" {
  inherits   = ["_common"]
  dockerfile = "docker/Dockerfile.gitserver"
  platforms  = MULTI_ARCH
  tags       = ["${REGISTRY}-gitserver:${VERSION}"]
}

target "worker-cbomkit" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-cbomkit"
  tags       = ["${REGISTRY}-worker-cbomkit:${VERSION}"]
}

target "worker-deepseek" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-deepseek"
  tags       = ["${REGISTRY}-worker-deepseek:${VERSION}"]
}

target "worker-chatgpt" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-chatgpt"
  tags       = ["${REGISTRY}-worker-chatgpt:${VERSION}"]
}

target "worker-claude" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-claude"
  tags       = ["${REGISTRY}-worker-claude:${VERSION}"]
}

target "worker-cryptoscan" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-cryptoscan"
  tags       = ["${REGISTRY}-worker-cryptoscan:${VERSION}"]
}

target "worker-pqcheck" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-pqcheck"
  tags       = ["${REGISTRY}-worker-pqcheck:${VERSION}"]
}
target "worker-qdarpan" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-qdarpan"
  tags       = ["${REGISTRY}-worker-qdarpan:${VERSION}"]
}
target "worker-securin-xbom" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-securin-xbom"
  tags       = ["${REGISTRY}-worker-securin-xbom:${VERSION}"]
}
target "worker-cipherradar" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-cipherradar"
  tags       = ["${REGISTRY}-worker-cipherradar:${VERSION}"]
}

target "worker-cbomkit-jar" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-cbomkit-jar"
  tags       = ["${REGISTRY}-worker-cbomkit-jar:${VERSION}"]
}

target "worker-cdxgen" {
  inherits   = ["_common"]
  dockerfile = "docker/Dockerfile.worker-cdxgen"
  platforms  = MULTI_ARCH
  tags       = ["${REGISTRY}-worker-cdxgen:${VERSION}"]
}

target "misc-pyqun" {
  inherits   = ["_common"]
  dockerfile = "docker/Dockerfile.misc-pyqun"
  platforms  = MULTI_ARCH
  tags       = ["${REGISTRY}-misc-pyqun:${VERSION}"]
}

# amd64-only: CodeQL ships no Linux arm64 binaries
target "worker-cryptobomforge" {
  inherits   = ["_common"]
  dockerfile = "docker/Dockerfile.worker-cryptobomforge"
  platforms  = ["linux/amd64"]
  tags       = ["${REGISTRY}-worker-cryptobomforge:${VERSION}"]
}

# amd64-only: links against the prebuilt amd64 OR-Tools release (dev profile; not in default group)
target "misc-treesimilarity" {
  inherits   = ["_common"]
  dockerfile = "docker/Dockerfile.misc-treesimilarity"
  platforms  = ["linux/amd64"]
  tags       = ["${REGISTRY}-misc-treesimilarity:${VERSION}"]
}

target "worker-xgrep" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-xgrep"
  tags       = ["${REGISTRY}-worker-xgrep:${VERSION}"]
}

target "worker-ciphersift" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-ciphersift"
  tags       = ["${REGISTRY}-worker-ciphersift:${VERSION}"]
}

target "worker-scanoss" {
  inherits   = ["_from-base"]
  dockerfile = "docker/Dockerfile.worker-scanoss"
  tags       = ["${REGISTRY}-worker-scanoss:${VERSION}"]
}
