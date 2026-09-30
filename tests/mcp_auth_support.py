"""Offline signing fixture: no Auth0, database server or network."""
import json
import time
from unittest.mock import patch
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from app.mcp_auth import AuthConfig, Principal, SCOPES

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CONFIG = AuthConfig('https://tenant.example/', 'https://family.example/mcp', {
    'auth0|owner': {'actor': 'Owner', 'permissions': sorted(SCOPES)},
    'auth0|reader': {'actor': 'Reader', 'permissions': ['expenses:read']},
})
ENV = {'MCP_AUTH_ISSUER': CONFIG.issuer, 'MCP_RESOURCE_URL': CONFIG.resource,
       'MCP_MEMBERS_JSON': json.dumps(CONFIG.members)}
ADMIN = Principal('auth0|owner', 'Owner', SCOPES)

def token(**overrides):
    claims = dict(iss=CONFIG.issuer, aud=CONFIG.resource, sub='auth0|owner',
                  iat=int(time.time())-1, exp=int(time.time())+300,
                  scope=' '.join(sorted(SCOPES)), permissions=sorted(SCOPES))
    claims.update(overrides)
    return jwt.encode(claims, KEY, algorithm='RS256', headers={'kid': 'offline'})

def offline_keys():
    return patch('jwt.PyJWKClient.fetch_data', return_value={
        'keys': [dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key())),
                      kid='offline', use='sig', alg='RS256')]})
