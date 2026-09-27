"""Probe output samples (plan 01b1). Format = `wg show wg0 dump` as checked on jeff 2026-09-26 without
printing a key (interface line 4 tab-separated fields, peer lines 8, preshared key `(none)`, keepalive
`off`); content = the 2026-09-25 live facts: Shield uFsg… on 10.8.0.2 handshaking, iPhone 6TXd… on
10.8.0.3 idle 519 s, and the collision where the Shield push stole 10.8.0.2 from the Mac peer RHZq….
The keys are fakes; SERVER_PRIVATE proves the parser drops the interface line."""
NOW = 1790000000
SERVER_PRIVATE = "SERVERprivateKEYmustNEVERbeSTOREDanywhere00="
SERVER_PUBLIC = "SERVERpublicKEYxxxxxxxxxxxxxxxxxxxxxxxxxxx0="
SHIELD = "uFsgSHIELDpublicKEYxxxxxxxxxxxxxxxxxxxxxxxx0="
IPHONE = "6TXdIPHONEpublicKEYxxxxxxxxxxxxxxxxxxxxxxxx0="
MAC = "RHZqMACpublicKEYxxxxxxxxxxxxxxxxxxxxxxxxxxx0="
IFACE = f"{SERVER_PRIVATE}\t{SERVER_PUBLIC}\t51820\toff"
SHIELD_LINE = f"{SHIELD}\t(none)\t198.51.100.7:51413\t10.8.0.2/32\t{NOW - 40}\t7440912\t257689968\toff"
IPHONE_LINE = f"{IPHONE}\t(none)\t203.0.113.77:62011\t10.8.0.3/32\t{NOW - 519}\t2653704\t25636716\toff"
MAC_LINE = f"{MAC}\t(none)\t(none)\t(none)\t0\t0\t0\toff"
CONF = "AllowedIPs = 10.8.0.2/32\nAllowedIPs = 10.8.0.3/32\n"
HEALTHY = f"active\n---\n{IFACE}\n{SHIELD_LINE}\n{IPHONE_LINE}\n---\n{CONF}"
COLLISION = f"active\n---\n{IFACE}\n{MAC_LINE}\n{IPHONE_LINE}\n{SHIELD_LINE}\n---\n{CONF}AllowedIPs = 10.8.0.2/32\n"
WG_DOWN = f"inactive\n---\n---\n{CONF}"            # wg show fails on stderr only; wg0.conf is still there
