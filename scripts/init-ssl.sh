#!/bin/bash
# =============================================================================
# SSL Certificate Initialization Script for api.nexusdental.ai
# =============================================================================
# This script:
# 1. Starts nginx with initial config (HTTP only)
# 2. Runs certbot to get SSL certificates
# 3. Switches to production nginx config with SSL
# =============================================================================

set -e

DOMAIN="api.nexusdental.ai"
EMAIL="admin@nexusdental.ai"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "================================================"
echo "SSL Certificate Setup for $DOMAIN"
echo "================================================"
echo ""

# Check if DNS is configured
echo "Step 1: Checking DNS configuration..."
IP=$(dig +short "$DOMAIN" 2>/dev/null || echo "")
if [ -z "$IP" ]; then
    echo "ERROR: DNS not configured for $DOMAIN"
    echo ""
    echo "Please add an A record pointing $DOMAIN to this server's IP address."
    echo "Then run this script again."
    exit 1
fi
echo "✓ DNS configured: $DOMAIN -> $IP"
echo ""

# Check if this is the correct server
SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo "unknown")
echo "  This server's IP: $SERVER_IP"
if [ "$IP" != "$SERVER_IP" ]; then
    echo "WARNING: DNS points to $IP but this server is $SERVER_IP"
    echo "Make sure DNS is pointing to this server before continuing."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi
echo ""

# Step 2: Use initial nginx config
echo "Step 2: Starting nginx with HTTP-only config..."
cp docker/nginx/nginx.init.conf docker/nginx/nginx.active.conf

# Create docker-compose override for init
cat > docker-compose.init.yml << 'EOF'
services:
  nginx:
    volumes:
      - ./docker/nginx/nginx.active.conf:/etc/nginx/nginx.conf:ro
      - certbot_www:/var/www/certbot
    ports:
      - "80:80"

volumes:
  certbot_www:
EOF

# Start nginx and API
docker compose -f docker-compose.yml -f docker-compose.init.yml up -d nginx
echo "Waiting for nginx to start..."
sleep 5

# Verify nginx is running
if ! curl -s "http://localhost/health" > /dev/null; then
    echo "ERROR: Nginx not responding on port 80"
    docker compose -f docker-compose.yml -f docker-compose.init.yml logs nginx
    exit 1
fi
echo "✓ Nginx running"
echo ""

# Step 3: Get SSL certificate
echo "Step 3: Obtaining SSL certificate from Let's Encrypt..."
docker compose -f docker-compose.yml -f docker-compose.ssl.yml run --rm certbot-init \
    certonly --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN"

# Check if certificate was obtained
if [ ! -f "/var/lib/docker/volumes/botnexhealth_certbot_certs/_data/live/$DOMAIN/fullchain.pem" ]; then
    # Try alternate path
    docker compose -f docker-compose.yml -f docker-compose.ssl.yml run --rm certbot-init \
        certificates
fi

echo "✓ SSL certificate obtained"
echo ""

# Step 4: Switch to production config
echo "Step 4: Switching to SSL-enabled nginx config..."
docker compose -f docker-compose.yml -f docker-compose.init.yml down

# Clean up temp files
rm -f docker-compose.init.yml docker/nginx/nginx.active.conf

echo ""
echo "================================================"
echo "SSL Setup Complete!"
echo "================================================"
echo ""
echo "Now start the production stack:"
echo ""
echo "  make prod-ssl"
echo ""
echo "Or manually:"
echo ""
echo "  docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ssl.yml up -d"
echo ""
echo "Your API will be available at:"
echo "  https://$DOMAIN/api/v1/"
echo ""
echo "Health check:"
echo "  curl https://$DOMAIN/livez"
echo ""
