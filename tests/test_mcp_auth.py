import asyncio
import unittest
from unittest.mock import patch
import jwt
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from app.mcp_auth import (AuthConfig, AuthFailure, McpBearerMiddleware,
                          TokenVerifier, principal, READ, WRITE, LINKS, required_scopes)
from app.mcp_server import build_mcp
from app.db import Database
from app.store import Store
from app.web import build_routes, session_middleware
from mcp_auth_support import CONFIG, ENV, ADMIN, token, offline_keys

class OAuthBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.keys = offline_keys(); self.keys.start(); self.addCleanup(self.keys.stop)
        db = Database('sqlite:///:memory:'); db.init(); self.store = Store(db)
        self.mcp = build_mcp(self.store)
        app = self.mcp.streamable_http_app()
        app.router.routes.extend(build_routes(self.store))
        self.client = self.enterContext(TestClient(McpBearerMiddleware(app, config=CONFIG)))

    def call(self, name, arguments=None, bearer=None):
        return self.client.post('/mcp', headers={
            'Accept': 'application/json, text/event-stream',
            'Authorization': 'Bearer ' + (bearer or token())},
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                  'params': {'name': name, 'arguments': arguments or {}}})

    def assert_scope_challenge(self, response, scopes):
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()['result']
        self.assertTrue(result['isError'])
        challenge = result['_meta']['mcp/www_authenticate'][0]
        self.assertEqual(challenge, response.headers['www-authenticate'])
        self.assertIn('error="insufficient_scope"', challenge)
        self.assertIn('error_description=', challenge)
        self.assertIn(f'scope="{scopes}"', challenge)
        self.assertIn(CONFIG.metadata_url, challenge)

    def test_wire_oauth_descriptors_are_canonical_and_mirrored(self):
        r = self.client.post('/mcp', headers={
            'Accept':'application/json, text/event-stream',
            'Authorization':'Bearer '+token()},
            json={'jsonrpc':'2.0','id':1,'method':'tools/list'})
        self.assertEqual(r.status_code, 200, r.text)
        tools=r.json()['result']['tools']
        self.assertEqual(len(tools), 18)
        for tool in tools:
            expected=[{'type':'oauth2','scopes':required_scopes(tool['name'])}]
            self.assertIn('securitySchemes', tool)
            self.assertEqual(tool['securitySchemes'], expected)
            self.assertEqual(tool['_meta']['securitySchemes'], expected)

    def test_malformed_authenticated_body_never_reaches_root_logs(self):
        import logging
        sentinel='LEDGER_SENTINEL_METHOD_AND_AMOUNT'
        with self.assertLogs(level=logging.WARNING) as capture:
            r=self.client.post('/mcp', headers={
                'Accept':'application/json, text/event-stream',
                'Authorization':'Bearer '+token()},
                json={'jsonrpc':'2.0','id':1,'method':sentinel,
                      'params':{'description':sentinel}})
            logging.warning('unrelated diagnostic is preserved')
        self.assertEqual(r.status_code, 200)
        self.assertIn('error', r.json())
        self.assertTrue(any(record.name=='root' and 'mcp/' in record.pathname for record in capture.records))
        for record in capture.records:
            self.assertNotIn(sentinel, record.getMessage())
            self.assertNotIn(sentinel, repr(record.args))
            self.assertIsNone(record.exc_info)
        self.assertIn('unrelated diagnostic is preserved', '\n'.join(capture.output))

    def test_discovery(self):
        for path in ('/.well-known/oauth-protected-resource', '/.well-known/oauth-protected-resource/mcp'):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), CONFIG.metadata())
        r = self.client.get('/mcp')
        self.assertEqual(r.status_code, 401)
        self.assertIn(CONFIG.metadata_url, r.headers['www-authenticate'])

    def test_no_static_secret_cookie_or_query_bypass(self):
        with patch.dict('os.environ', {'MCP_SECRET': 'legacy'}):
            for path in ('/mcp', '/mcp/', '/mcp/anything', '/mcp?access_token='+token()):
                self.assertEqual(self.client.get(path, headers={'Cookie': 'session=fake'}).status_code, 401)
            self.assertEqual(self.call('expenses_list', bearer='legacy').status_code, 401)

    def test_invalid_tokens(self):
        for overrides in ({'iss':'https://wrong.example/'}, {'aud':'other'},
                          {'exp':1}, {'nbf':9999999999}, {'sub':None},
                          {'permissions':'expenses:read'}, {'scope':[]},
                          {'exp':float('inf')}, {'iat':True}, {'nbf':float('nan')}):
            r = self.call('expenses_list', bearer=token(**overrides))
            self.assertEqual(r.status_code, 401, overrides)
            self.assertIn('invalid_token', r.headers['www-authenticate'])
        wrong_key = jwt.encode({'iss':CONFIG.issuer}, 'wrong', algorithm='HS256')
        self.assertEqual(self.call('expenses_list', bearer=wrong_key).status_code, 401)
        parts = token().split('.'); parts[-1] = 'invalid'
        self.assertEqual(self.call('expenses_list', bearer='.'.join(parts)).status_code, 401)

    def test_required_claims(self):
        from mcp_auth_support import KEY
        for missing in ('iss', 'aud', 'exp', 'iat', 'sub'):
            claims = jwt.decode(token(), options={'verify_signature':False})
            claims.pop(missing)
            bad = jwt.encode(claims, KEY, algorithm='RS256', headers={'kid':'offline'})
            self.assertEqual(self.call('expenses_list', bearer=bad).status_code, 401)

    def test_nonmember_and_scope_intersection(self):
        self.assertEqual(self.call('expenses_list', bearer=token(sub='auth0|stranger')).status_code, 403)
        for overrides in ({'scope':READ}, {'permissions':[READ]}, {'sub':'auth0|reader'}):
            r = self.call('expenses_add', {'amount':'10'}, token(**overrides))
            self.assert_scope_challenge(r, READ+' '+WRITE)
            self.assertIn(WRITE, r.headers['www-authenticate'])
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.call('expenses_list', bearer=token(scope='')).status_code, 403)

    def test_authorized_write_read_and_attribution(self):
        r = self.call('expenses_add', {'amount':'10','description':'fixture', 'submitted_by':'Impersonated'})
        self.assertEqual(r.status_code, 200, r.text)
        row = self.store.list()[0]
        self.assertEqual(row.submitted_by, 'Owner')
        r = self.call('expenses_mark_paid', {'expense_id':row.id, 'changed_by':'Impersonated'})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.store.history(row.id)[-1].changed_by, 'Owner')
        r = self.call('expenses_list', bearer=token(sub='auth0|reader', scope=READ, permissions=[READ]))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('fixture', r.text)

    def test_all_tools_have_permissions_and_oauth_metadata(self):
        from app.mcp_auth import TOOL_SCOPES
        tools = asyncio.run(self.mcp.list_tools())
        self.assertEqual({t.name for t in tools}, set(TOOL_SCOPES))
        for tool in tools:
            self.assertEqual(tool.meta['securitySchemes'], [{'type':'oauth2', 'scopes':required_scopes(tool.name)}])
            if TOOL_SCOPES[tool.name] != READ:
                self.assert_scope_challenge(self.call(tool.name, bearer=token(scope=READ)), ' '.join(required_scopes(tool.name)))

    def test_link_management(self):
        for name in ('expenses_list_links','expenses_mint_link','expenses_revoke_link'):
            r = self.call(name, {'token_or_id':'fixture'}, token(scope=READ+' '+WRITE))
            self.assert_scope_challenge(r, READ+' '+LINKS)
            self.assertIn(LINKS, r.headers['www-authenticate'])
        self.assertEqual(self.store.list_tokens(), [])
        r = self.call('expenses_mint_link', {'label':'offline'})
        self.assertEqual(r.status_code, 200, r.text)
        link_id = self.store.list_tokens()[0]['id']
        self.assertEqual(self.call('expenses_list_links').status_code, 200)
        self.assertEqual(self.call('expenses_revoke_link', {'token_or_id':link_id}).status_code, 200)
        self.assertTrue(self.store.list_tokens()[0]['revoked'])

    def test_direct_execution_is_also_guarded(self):
        async def direct():
            with self.assertRaises(Exception):
                await self.mcp.call_tool('expenses_add', {'amount':'10'})
        asyncio.run(direct())
        self.assertEqual(self.store.list(), [])

    def test_malformed_and_oversized_requests(self):
        headers={'Authorization':'Bearer '+token()}
        for body in ('[{}]', '{', '{"method":"tools/call","params":[]}'):
            self.assertEqual(self.client.post('/mcp', headers=headers, content=body).status_code, 400)
        self.assertEqual(self.client.post('/mcp', headers=headers, content='x'*1048577).status_code, 413)

    def test_jwks_outage_fails_closed(self):
        with patch('jwt.PyJWKClient.fetch_data', side_effect=jwt.PyJWKClientConnectionError('sensitive')):
            r=self.call('expenses_list')
            self.assertEqual(r.status_code, 401)
            self.assertNotIn('sensitive', r.text)

    def test_portal_and_health_unchanged(self):
        self.assertEqual(self.client.get('/health').status_code, 200)
        self.assertEqual(self.client.get('/t/invalid').status_code, 404)

    def test_protocol_logs_cannot_print_payloads(self):
        import logging
        import io
        from app.mcp_auth import protect_protocol_logs
        logger = logging.getLogger('mcp.security_fixture')
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        protect_protocol_logs()
        logger.error('credential=%s ledger=%s', 'TOKEN_SENTINEL', 'LEDGER_SENTINEL')
        self.assertNotIn('TOKEN_SENTINEL', stream.getvalue())
        self.assertNotIn('LEDGER_SENTINEL', stream.getvalue())
        self.assertIn('MCP protocol event', stream.getvalue())

    def test_direct_read_only_identity_cannot_write(self):
        from app.mcp_auth import Principal
        async def direct():
            ctx = principal.set(Principal('auth0|reader', 'Reader', frozenset([READ])))
            try:
                with self.assertRaises(Exception):
                    await self.mcp.call_tool('expenses_add', {'amount':'10'})
            finally:
                principal.reset(ctx)
        asyncio.run(direct())
        self.assertEqual(self.store.list(), [])

    def test_identity_context_does_not_survive_a_request(self):
        self.assertEqual(self.call('expenses_list').status_code, 200)
        self.assertEqual(self.client.get('/mcp').status_code, 401)
        self.assertIsNone(principal.get())

    def test_duplicate_authorization_headers_are_rejected(self):
        r=self.client.get('/mcp', headers=[('Authorization','Bearer '+token()),
                                          ('Authorization','Bearer '+token())])
        self.assertEqual(r.status_code, 401)

class JwksRefreshTests(unittest.TestCase):
    def key_token(self, kid):
        from mcp_auth_support import KEY
        claims=jwt.decode(token(), options={'verify_signature':False})
        return jwt.encode(claims, KEY, algorithm='RS256', headers={'kid':kid})

    def test_distinct_unknown_keys_coalesce_and_rotation_recovers(self):
        import anyio
        async def exercise():
            now=[0.0]
            verifier=TokenVerifier(CONFIG, clock=lambda: now[0])
            with offline_keys() as fetch:
                async def reject(kid):
                    with self.assertRaises(AuthFailure):
                        await verifier.verify(self.key_token(kid))
                async with anyio.create_task_group() as group:
                    for index in range(20): group.start_soon(reject, f'unknown-{index}')
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual((await verifier.verify(token())).subject, 'auth0|owner')
                self.assertEqual(fetch.call_count, 1)
                now[0]=60.0
                data=fetch.return_value
                data['keys'][0]['kid']='rotated'
                self.assertEqual((await verifier.verify(self.key_token('rotated'))).subject, 'auth0|owner')
                self.assertEqual(fetch.call_count, 2)
                for index in range(10): await reject(f'other-{index}')
                self.assertEqual(fetch.call_count, 2)
        anyio.run(exercise)

    def test_outage_retries_are_bounded_and_expired_cache_denied(self):
        import anyio
        async def exercise():
            now=[0.0]; verifier=TokenVerifier(CONFIG, clock=lambda:now[0])
            with offline_keys() as fetch:
                await verifier.verify(token())
                now[0]=301.0
                fetch.side_effect=jwt.PyJWKClientConnectionError('sensitive')
                for _ in range(10):
                    with self.assertRaises(AuthFailure): await verifier.verify(token())
                self.assertEqual(fetch.call_count, 2)
                now[0]=361.0
                fetch.side_effect=None
                await verifier.verify(token())
                self.assertEqual(fetch.call_count, 3)
        anyio.run(exercise)

    def test_jwks_fetch_does_not_borrow_portal_worker_limiter(self):
        import anyio
        import threading
        async def exercise():
            limiter=anyio.to_thread.current_default_thread_limiter()
            previous=limiter.total_tokens
            limiter.total_tokens=1
            started=anyio.Event(); release=threading.Event()
            def portal_worker():
                anyio.from_thread.run_sync(started.set)
                release.wait(2)
            async def occupy(): await anyio.to_thread.run_sync(portal_worker)
            try:
                async with anyio.create_task_group() as group:
                    group.start_soon(occupy)
                    await started.wait()
                    self.assertEqual(limiter.borrowed_tokens, 1)
                    verifier=TokenVerifier(CONFIG)
                    self.assertIsNot(verifier.jwks_limiter, limiter)
                    with offline_keys(), anyio.fail_after(1):
                        await verifier.verify(token())
                    self.assertEqual(limiter.borrowed_tokens, 1)
                    release.set()
            finally:
                release.set(); limiter.total_tokens=previous
        anyio.run(exercise)

class ConfigurationTests(unittest.TestCase):
    def test_missing_config_blocks_mcp_locally_and_startup_in_production(self):
        with patch.dict('os.environ', {}, clear=True):
            app=McpBearerMiddleware(Starlette())
            self.assertEqual(TestClient(app).get('/mcp').status_code, 503)
        with patch.dict('os.environ', {'K_SERVICE':'family-expenses'}, clear=True):
            with self.assertRaises(ValueError):
                McpBearerMiddleware(Starlette())
            from app.main import build_asgi_app
            with patch('app.main.Database') as database:
                with self.assertRaises(ValueError): build_asgi_app()
                database.assert_not_called()

    def test_invalid_configuration(self):
        for name, value in [('MCP_AUTH_ISSUER','http://tenant.example/'),
                            ('MCP_AUTH_ISSUER','https://tenant.example/no/'),
                            ('MCP_RESOURCE_URL','https://family.example/other'),
                            ('MCP_RESOURCE_URL','https://family.example/mcp?x=1'),
                            ('MCP_MEMBERS_JSON','{}'), ('MCP_MEMBERS_JSON','[]'),
                            ('MCP_MEMBERS_JSON','{"user":{"actor":"X","permissions":["admin"]}}')]:
            with patch.dict('os.environ', dict(ENV, **{name:value}), clear=True):
                with self.assertRaises(ValueError): AuthConfig.from_env()
