PROFILE ?= personal
REGION  ?= sa-east-1

export AWS_PROFILE=$(PROFILE)
export AWS_REGION=$(REGION)

.DEFAULT_GOAL := help

help: ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## create VPS + WireGuard (iPhone QR + dist/<peer>-vpn.conf; pushes the Shield)
	@bash setup.sh

teardown: ## destroy all AWS resources (no leftover charges)
	@bash teardown.sh

swap-ip: ## release current IP and attach a fresh one
	@bash swap-ip.sh

tf-setup: ## create VPS + WireGuard via Terraform (auto-fetches mac-vpn.conf + iPhone QR)
	@cd terraform && terraform init -input=false && \
		TF_VAR_aws_region=$(REGION) TF_VAR_availability_zone=$(REGION)a terraform apply
	@$(MAKE) config

tf-teardown: ## destroy all Terraform-managed resources
	@cd terraform && \
		TF_VAR_aws_region=$(REGION) TF_VAR_availability_zone=$(REGION)a terraform destroy

config: ## re-fetch client configs from the running Terraform server
	@bash fetch-config.sh \
		"$$(cd terraform && terraform output -raw server_ip)" \
		"$$(cd terraform && terraform output -raw ssh_private_key_path)"

status: ## show server state and WireGuard peers
	@uv run --project cli vps-lightsail status --region $(REGION) --profile $(PROFILE)

.PHONY: help setup teardown swap-ip tf-setup tf-teardown config status
