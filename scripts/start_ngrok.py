#!/usr/bin/env python3
import os
import sys
import logging
from pyngrok import ngrok

# Add the project root to the path
sys.path.append(os.getcwd())


def start_ngrok():
    """Start ngrok tunnel to localhost:8000."""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("ngrok_helper")

    # Read auth token from environment variable if present, otherwise rely on pyngrok config
    ngrok_auth_token = os.getenv("NGROK_AUTH_TOKEN")
    if ngrok_auth_token:
        ngrok.set_auth_token(ngrok_auth_token)

    # Start tunnel
    port = 8000
    try:
        # Create a public URL for the local web server
        public_url = ngrok.connect(port).public_url
        logger.info(f"Ngrok Tunnel Started: {public_url} -> http://localhost:{port}")
        
        # Display helper message
        print("\n" + "="*60)
        print(f"  Public URL: {public_url}")
        print(f"  API Docs:   {public_url}/docs")
        print("  Base URL for Retell: " + public_url)
        print("="*60 + "\n")

        # Keep the process alive
        ngrok_process = ngrok.get_ngrok_process()
        ngrok_process.proc.wait()
    except KeyboardInterrupt:
        logger.info("Shutting down ngrok tunnel...")
        ngrok.kill()

if __name__ == "__main__":
    start_ngrok()
