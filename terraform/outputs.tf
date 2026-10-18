output "server_ip" {
  description = "Static public IP of the VPN server"
  value       = aws_lightsail_static_ip.vpn.ip_address
}

output "ssh_command" {
  description = "SSH command to connect to the server"
  value       = "ssh -i ${var.ssh_private_key_path} ubuntu@${aws_lightsail_static_ip.vpn.ip_address}"
}

output "ssh_private_key_path" {
  description = "Path to the SSH private key used for provisioning"
  value       = var.ssh_private_key_path
}

output "next_steps" {
  description = "What to do after apply"
  value       = <<-EOT
    Server ready at ${aws_lightsail_static_ip.vpn.ip_address}

    `make tf-setup` then runs `make config`: dist/shield-vpn.conf + the iPhone QR.
    To re-fetch the client configs later (e.g. after swap-ip.sh):
      make config
  EOT
}
