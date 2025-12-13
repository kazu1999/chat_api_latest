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
# 3. Fallback: Default to 'ueki' for backward compatibility during migration.

def get_client_id(event: dict) -> str:
    # 1. Check explicit header (for Realtime API server or debug)
    headers = event.get("headers") or {}
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

    # 3. Fallback default
    return "ueki"

