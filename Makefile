# Geocoder — dev gates and the air-gapped release.
#
#   make airgap USB=/run/media/$USER/DATOS   # build, verify and copy everything
#
# The air-gapped targets run on a host WITH internet; the target machine only
# ever sees the USB. Procedure and pitfalls: know-how/deploying-airgapped.md.

SHELL := /bin/bash
.DEFAULT_GOAL := help

DOCKER_VERSION  ?= 29.7.1
COMPOSE_VERSION ?= v5.3.1
DOCKER_BUNDLE   := dist/docker-offline-$(DOCKER_VERSION).tar.gz
BUNDLE          := geocoder-docker.tar.gz
# Flags for deploy/bake-data.sh: --rebuild (~2 h ETL) or --allow-stale-data.
BAKE_FLAGS      ?=

.PHONY: help test lint serve airgap airgap-bundle airgap-docker airgap-smoke usb

help:
	@echo "Dev"
	@echo "  make test            unit + ETL + API suite"
	@echo "  make lint            ruff"
	@echo "  make serve           API + UI on :8000 (needs a built DB)"
	@echo "Air-gapped release (build host with internet)"
	@echo "  make airgap-bundle   bake + $(BUNDLE)      BAKE_FLAGS=--allow-stale-data|--rebuild"
	@echo "  make airgap-docker   $(DOCKER_BUNDLE) (Docker for a machine without it)"
	@echo "  make airgap-smoke    install the bundle in a scratch dir and gate it"
	@echo "  make usb             copy bundle + installers + README to USB=<mountpoint>"
	@echo "                      (EXAMPLE_XLSX=<file> adds a spreadsheet to try Lote with)"
	@echo "  make airgap          all of the above, in order"

test:
	uv run pytest

lint:
	uv run ruff check src tests

serve:
	uv run geocoder serve --port 8000

airgap-bundle:
	deploy/build-airgapped.sh $(BAKE_FLAGS)

# Static Docker binaries + compose plugin, checksummed. Kept across runs: the
# file only changes when DOCKER_VERSION does.
airgap-docker: $(DOCKER_BUNDLE)
$(DOCKER_BUNDLE):
	mkdir -p dist
	DOCKER_VERSION=$(DOCKER_VERSION) COMPOSE_VERSION=$(COMPOSE_VERSION) \
	  DOCKER_BUNDLE_OUT=$(CURDIR)/$(DOCKER_BUNDLE) bash deploy/docker-offline/build-docker-bundle.sh

airgap-smoke:
	deploy/airgap-smoke.sh

usb: $(DOCKER_BUNDLE)
	USB="$(USB)" DOCKER_BUNDLE=$(DOCKER_BUNDLE) EXAMPLE_XLSX="$(EXAMPLE_XLSX)" deploy/update-usb-drive.sh

airgap: airgap-bundle airgap-docker airgap-smoke usb
