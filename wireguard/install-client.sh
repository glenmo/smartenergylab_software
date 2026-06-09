#!/bin/bash
# WireGuard client installer for desky / rubberduck.
#
# Solves the problem that the standard wg0.conf for each host is
# multi-line — pasting into a remote shell can split long lines or
# auto-indent the heredoc terminator, leaving the config broken in
# subtle ways. This script writes the whole file from a single curl |
# bash invocation, no pasting required.
#
# Usage on the host (must already have ~/<hostname>.key generated):
#   curl -sL https://raw.githubusercontent.com/glenmo/smartenergylab_software/main/wireguard/install-client.sh | sudo bash
#
# Adjust the IP / pubkey constants below if the burgan side changes.

set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "Run as root, e.g.:" >&2
    echo "  curl -sL https://raw.githubusercontent.com/glenmo/smartenergylab_software/main/wireguard/install-client.sh | sudo bash" >&2
    exit 1
fi

# --- Constants (edit if the burgan side ever moves) -------------------
BURGAN_PUB="9/lmaJj0RESugwHjRPhqy8UNgZmwgLykR0Admm83dFI="
BURGAN_ENDPOINT="burgan.arachnoid.net.au:41820"
SUBNET="10.13.13.0/24"

# --- Per-host IP map --------------------------------------------------
HOSTNAME_ME="$(hostname)"
case "$HOSTNAME_ME" in
    desky)      WG_IP=10.13.13.7 ;;
    rubberduck) WG_IP=10.13.13.8 ;;
    *)
        echo "Unknown host '$HOSTNAME_ME'. This script only knows desky / rubberduck." >&2
        echo "Edit /etc/wireguard/wg0.conf by hand, or extend the case in this script." >&2
        exit 1
        ;;
esac

# --- Locate the private key file --------------------------------------
USER_HOME="$(getent passwd "${SUDO_USER:-root}" | cut -d: -f6)"
KEY_FILE="$USER_HOME/$HOSTNAME_ME.key"

if [[ ! -r "$KEY_FILE" ]]; then
    echo "Private key not found at $KEY_FILE." >&2
    echo "Generate it first as the regular user:" >&2
    echo "  umask 077 && cd ~ && wg genkey | tee $HOSTNAME_ME.key | wg pubkey > $HOSTNAME_ME.pub" >&2
    echo "Then share the contents of ~/$HOSTNAME_ME.pub with whoever runs the burgan side." >&2
    exit 1
fi
PRIV_KEY="$(tr -d '[:space:]' < "$KEY_FILE")"

# --- Install wireguard if missing -------------------------------------
if ! command -v wg >/dev/null 2>&1; then
    echo "Installing wireguard-tools..."
    apt-get update -qq
    apt-get install -y wireguard wireguard-tools
fi

# --- Write the config -------------------------------------------------
CONF="/etc/wireguard/wg0.conf"
TMP="$(mktemp)"
cat > "$TMP" <<EOL
[Interface]
Address    = $WG_IP/24
PrivateKey = $PRIV_KEY

[Peer]
PublicKey            = $BURGAN_PUB
Endpoint             = $BURGAN_ENDPOINT
AllowedIPs           = $SUBNET
PersistentKeepalive  = 25
EOL
install -m 600 -o root -g root "$TMP" "$CONF"
rm -f "$TMP"

echo "Wrote $CONF for $HOSTNAME_ME ($WG_IP/24)."
echo "  Burgan endpoint: $BURGAN_ENDPOINT"
echo "  Burgan pubkey:   $BURGAN_PUB"
PUB_FILE="$USER_HOME/$HOSTNAME_ME.pub"
if [[ -r "$PUB_FILE" ]]; then
    echo "Your public key (give this to the burgan admin):"
    echo "  $(cat "$PUB_FILE")"
fi

echo ""
echo "Restarting wg-quick@wg0..."
systemctl enable wg-quick@wg0 >/dev/null 2>&1 || true
systemctl restart wg-quick@wg0
sleep 1

echo ""
echo "Status:"
wg show wg0
echo ""
echo "Reaching burgan over the tunnel:"
if ping -c 2 -W 3 10.13.13.1 >/dev/null; then
    echo "  ✓ ping 10.13.13.1 OK — tunnel is up."
else
    echo "  ✗ ping 10.13.13.1 failed — check that burgan has registered this host's public key" >&2
    echo "    in its /etc/wireguard/wg0.conf and run 'sudo wg syncconf wg0 <stripped config>'." >&2
    exit 2
fi
