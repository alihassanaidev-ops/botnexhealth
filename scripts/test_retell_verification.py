
import os
import sys
import logging
from dotenv import load_dotenv

# This is a manual script, not a pytest test module.
__test__ = False

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from src.app.retell.security import RetellSignatureVerifier

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_verification():
    load_dotenv()
    api_key = os.getenv("RETELL_API_SECRET")
    if not api_key:
        logger.error("RETELL_API_SECRET not found in environment")
        return

    verifier = RetellSignatureVerifier(api_key)
    try:
        from retell.lib.webhook_auth import sign_request_body
    except Exception as exc:
        logger.error("Could not import Retell signing helper: %s", exc)
        return

    # Case 1: Compact JSON
    payload_compact = '{"foo":"bar"}'
    signature_compact = sign_request_body(payload_compact, api_key)
    
    logger.info(f"Testing Compact JSON: {payload_compact}")
    if verifier.verify_signature(payload_compact, signature_compact):
        logger.info("PASS: Compact JSON verified")
    else:
        logger.error("FAIL: Compact JSON failed verification")

    # Case 2: Spaced JSON
    payload_spaced = '{ "foo": "bar" }'
    signature_spaced = sign_request_body(payload_spaced, api_key)
    
    logger.info(f"Testing Spaced JSON: {payload_spaced}")
    if verifier.verify_signature(payload_spaced, signature_spaced):
        logger.info("PASS: Spaced JSON verified")
    else:
        logger.error("FAIL: Spaced JSON failed verification")

if __name__ == "__main__":
    try:
        test_verification()
    except ImportError:
        # Fallback if retell.lib.webhook_auth isn't importable (e.g. if I guessed the path wrong)
        # But src/app/retell/security.py imports it so it should work.
        print("Could not import retell library. Ensure it is installed.")
