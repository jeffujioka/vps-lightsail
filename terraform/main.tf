terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "peers" {
  description = "Peer names in config order; peers[i] gets 10.8.0.(2+i)"
  type        = list(string)
  default     = ["shield", "iphone"]
}

variable "static_ip_name" {
  type    = string
  default = "vps-vpn-static-ip" # same as the CLI and the agent (was "${instance_name}-ip")
}

# ── SSH key pair ──────────────────────────────────────────────────────────────

resource "aws_lightsail_key_pair" "vpn" {
  name       = "${var.instance_name}-key"
  public_key = file(pathexpand(var.ssh_public_key_path))
}

# ── Instance ──────────────────────────────────────────────────────────────────

resource "aws_lightsail_instance" "vpn" {
  name              = var.instance_name
  availability_zone = var.availability_zone
  blueprint_id      = var.blueprint_id
  bundle_id         = var.bundle_id
  key_pair_name     = aws_lightsail_key_pair.vpn.name

  connection {
    type        = "ssh"
    user        = "ubuntu"
    host        = self.public_ip_address
    private_key = file(pathexpand(var.ssh_private_key_path))
  }

  provisioner "file" {
    source      = "${path.module}/../provision.sh"
    destination = "/tmp/provision.sh"
  }

  provisioner "remote-exec" {
    inline = [
      "sudo VPS_PEERS='${join(" ", var.peers)}' bash /tmp/provision.sh",
    ]
  }
}

# ── Firewall ports ────────────────────────────────────────────────────────────

resource "aws_lightsail_instance_public_ports" "vpn" {
  instance_name = aws_lightsail_instance.vpn.name

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
  }

  port_info {
    protocol  = "udp"
    from_port = 51820
    to_port   = 51820
  }
}

# ── Static IP ─────────────────────────────────────────────────────────────────

resource "aws_lightsail_static_ip" "vpn" {
  name = var.static_ip_name
}

resource "aws_lightsail_static_ip_attachment" "vpn" {
  static_ip_name = aws_lightsail_static_ip.vpn.name
  instance_name  = aws_lightsail_instance.vpn.name
}
