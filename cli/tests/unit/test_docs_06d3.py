"""User docs follow the CLI migration (spec-06 §1, §1.1, §7): no PEM, no fetch-config.sh <ip>, swap note."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
README = (REPO / "README.md").read_text()
CLAUDE = (REPO / "CLAUDE.md").read_text()


def section(text: str, title: str) -> str:
    return text.split(title, 1)[1].split("\n## ", 1)[0]


def test_docs_describe_the_new_cli():
    for text in (README, CLAUDE):
        for dead in ("vps-vpn.pem", "mac-vpn.conf", "ssh -i", "fetch-config.sh <", "Mac peer: 10.8.0.2"):
            assert dead not in text, dead
        assert "vps-lightsail export" in text and "vps-lightsail push --peer shield" in text


def test_readme_swap_ip_note_and_teardown_scope():
    swap = section(README, "## Swap IP")
    assert "every peer config dies" in swap.lower() and "make config" in swap and "push --peer shield" in swap
    teardown = section(README, "## Teardown")
    assert "zero instances" in teardown and "static IP" in teardown and "dist/" in teardown


def test_claude_md_friend_flow_uses_the_cli():
    friend = section(CLAUDE, "## ADDING A FRIEND AS PEER")
    assert "vps-lightsail push --name friend" in friend and "dist/friend-vpn.conf" in friend
    assert "wg genkey" not in friend and "SERVER_IP=" not in friend
    assert "shield 10.8.0.2" in section(CLAUDE, "## HOW IT WORKS")
