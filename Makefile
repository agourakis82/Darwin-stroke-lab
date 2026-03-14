SHELL := /bin/bash

REPO_ROOT := $(CURDIR)
WRAPPER := $(REPO_ROOT)/scripts/run_fsharp_cluster_smoke.sh
BUNDLE_SCRIPT := $(REPO_ROOT)/scripts/prepare_cluster_sounio_bundle.sh
WORKER_IMAGE ?= darwin-research-os-worker:k3s-dev
SOUNIO_SOURCE_ROOT ?= $(HOME)/sounio-lang-sounio
SOUNIO_BINARY_ROOT ?= $(SOUNIO_SOURCE_ROOT)
SSH_HOST ?= demetrios@DEVdesktop

.PHONY: help prepare-cluster-sounio-bundle build-fsharp-worker-image import-fsharp-worker-image smoke-cluster-sounio smoke-cluster-benchmark smoke-cluster-sounio-fast smoke-cluster-benchmark-fast

help:
	@printf '%s\n' \
	  'Available targets:' \
	  '  prepare-cluster-sounio-bundle  Stage the Sounio bundle into fsharp/cluster-sounio' \
	  '  build-fsharp-worker-image      Build the Linux worker image used by the cluster smokes' \
	  '  import-fsharp-worker-image     Import the worker image into DEVdesktop k3s' \
	  '  smoke-cluster-sounio           Full one-command Sounio runtime cluster smoke' \
	  '  smoke-cluster-benchmark        Full one-command benchmark cluster smoke' \
	  '  smoke-cluster-sounio-fast      Reuse bundle/image/import and run only the control-plane smoke' \
	  '  smoke-cluster-benchmark-fast   Reuse bundle/image/import and run only the control-plane smoke'

prepare-cluster-sounio-bundle:
	bash "$(BUNDLE_SCRIPT)" "$(SOUNIO_SOURCE_ROOT)" "$(SOUNIO_BINARY_ROOT)"

build-fsharp-worker-image:
	docker buildx build --platform linux/amd64 --load \
		-f "$(REPO_ROOT)/fsharp/Dockerfile.worker" \
		-t "$(WORKER_IMAGE)" \
		"$(REPO_ROOT)"

import-fsharp-worker-image:
	docker save "$(WORKER_IMAGE)" | ssh -o BatchMode=yes "$(SSH_HOST)" 'sudo k3s ctr images import -'

smoke-cluster-sounio:
	bash "$(WRAPPER)" --mode sounio

smoke-cluster-benchmark:
	bash "$(WRAPPER)" --mode benchmark

smoke-cluster-sounio-fast:
	bash "$(WRAPPER)" --mode sounio --skip-bundle --skip-image --skip-import

smoke-cluster-benchmark-fast:
	bash "$(WRAPPER)" --mode benchmark --skip-bundle --skip-image --skip-import
