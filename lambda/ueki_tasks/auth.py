import os
import json
import base64
import time
from typing import Optional

# For Cognito JWT verification, we'd typically use a library like python-jose or PyJWT.
# However, standard Lambda environments don't include these by default without layers.
# For this implementation, we will trust the API Gateway Authorizer's result if available,
# or look for a specific header for server-to-server communication (x-client-id).
#
# Strategy:
# 1. If 'x-client-id' is present, use it (Assuming internal/protected access or development mode).
# 2. If valid Cognito claims are passed via requestContext (from Authorizer), use custom:tenant_id.
# 3. [NEW] Parse Authorization header (JWT) manually if Authorizer is disabled or bypassed.
# 4. Fallback: Default to 'ueki' for backward compatibility during migration.

def get_client_id(event: dict) -> str:
    headers = event.get("headers") or {}
    
    # 1. Check explicit header (for Realtime API server or debug)
    # headers are case-insensitive in APIGateway v2 usually, but let's be safe
    for k, v in headers.items():
        if k.lower() == "x-client-id":
            return v
    
    # 2. Check Authorizer context (if Cognito Authorizer is enabled in APIGateway)
    # requestContext -> authorizer -> jwt -> claims -> custom:tenant_id
    try:
        claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
        tenant = claims.get("custom:tenant_id")
        if tenant:
            return tenant
    except Exception:
        pass

    # 3. [NEW] Parse Authorization header (Bearer Token) manually
    # This acts as a fallback when API Gateway Authorizer is not enabled or for testing.
    # WARNING: This performs NO signature verification. Use only if API Gateway validates it
    # or in trusted environments. Ideally, use a Lambda Layer with python-jose.
    auth_header = None
    for k, v in headers.items():
        if k.lower() == "authorization":
            auth_header = v
            break
            
    if auth_header and auth_header.startswith("Bearer "):
        try:
            token = auth_header.split(" ")[1]
            parts = token.split('.')
            if len(parts) == 3:
                payload_segment = parts[1]
                # Add padding if needed
                rem = len(payload_segment) % 4
                if rem > 0:
                    payload_segment += '=' * (4 - rem)
                
                payload_bytes = base64.urlsafe_b64decode(payload_segment)
                payload = json.loads(payload_bytes)
                tenant = payload.get("custom:tenant_id")
                if tenant:
                    return tenant
        except Exception as e:
            print(f"JWT manual parsing failed: {e}")
            pass

    # 4. Fallback default
    return "ueki"
