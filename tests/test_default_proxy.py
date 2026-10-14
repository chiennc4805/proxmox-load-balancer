import asyncio
import gzip
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import unquote

import requests

from app.framework.contracts.proxmox import (
    ProxmoxAPI,
    ProxmoxTimeoutError,
    ProxmoxTransportError,
    UpstreamResponse,
)
from app.framework.contracts.request import EndpointRequest
from app.framework.workflows.default_proxy import DefaultProxyWorkflow
from app.gateway.controller import get_proxmox_client
from app.gateway.route_matcher import RouteMatcher
from app.framework.contracts.route import Route
from app.infrastructure.config.route_loader import read_routes
from app.infrastructure.proxmox.client import ProxmoxClient
from app.main import app
from app.settings import Settings


PAYLOAD = b'{"data":[{"node":"pve1"}]}'
COMPRESSED_PAYLOAD = gzip.compress(PAYLOAD)


class FakeProxmox(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self._respond()

    def do_POST(self):
        self._respond()

    def do_HEAD(self):
        self._respond()

    def _respond(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.captured.append({
            'method': self.command,
            'path': self.path,
            'headers': self.headers,
            'body': body,
        })

        if self.path.endswith('/redirect'):
            self.send_response(302)
            self.send_header('Location', '/api2/json/nodes')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return

        status = 403 if self.path.endswith('/forbidden') else 200
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Encoding', 'gzip')
        self.send_header('Content-Length', str(len(COMPRESSED_PAYLOAD)))
        self.send_header('Connection', 'close, X-Internal')
        self.send_header('X-Internal', 'must-not-be-forwarded')
        self.send_header('Set-Cookie', 'one=1')
        self.send_header('Set-Cookie', 'two=2')
        self.end_headers()

        if self.command != 'HEAD':
            self.wfile.write(COMPRESSED_PAYLOAD)


async def call_gateway(method, path, body=b'', headers=()):
    raw_path, _, raw_query = path.partition('?')
    messages = []
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {'type': 'http.request', 'body': body, 'more_body': False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    scope = {
        'type': 'http',
        'asgi': {'version': '3.0'},
        'http_version': '1.1',
        'method': method,
        'scheme': 'http',
        'path': unquote(raw_path),
        'raw_path': raw_path.encode('ascii'),
        'query_string': raw_query.encode('ascii'),
        'root_path': '',
        'headers': [(b'host', b'gateway.test'), *headers],
        'client': ('127.0.0.1', 12345),
        'server': ('gateway.test', 80),
    }
    await app(scope, receive, send)
    start = next(message for message in messages if message['type'] == 'http.response.start')
    response_body = b''.join(
        message.get('body', b'')
        for message in messages
        if message['type'] == 'http.response.body'
    )
    return start['status'], start['headers'], response_body


class DefaultProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeProxmox)
        cls.server.captured = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.settings = Settings(
            pve_api_host=f'http://127.0.0.1:{cls.server.server_port}',
            pve_api_user='service@pve',
            pve_token_name='gateway',
            pve_token_value='fake-secret',
            pve_verify_ssl=True,
            pve_request_timeout_seconds=2,
        )

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.captured.clear()
        self.client = ProxmoxClient(settings=self.settings)
        self.dependency_patch = patch.dict(
            app.dependency_overrides,
            {get_proxmox_client: lambda: self.client},
        )
        self.dependency_patch.start()
        self.addCleanup(self.dependency_patch.stop)

    def test_workflow_uses_injected_adapter(self):
        upstream = UpstreamResponse(200, ((b'content-type', b'application/json'),), PAYLOAD)
        proxmox = Mock(spec=ProxmoxAPI)
        proxmox.request = AsyncMock(return_value=upstream)
        request = EndpointRequest(
            method='POST',
            api_path='/nodes/pve1/qemu',
            raw_api_path=b'/nodes/pve1/qemu',
            raw_query_string=b'tag=one&tag=two',
            query_params=(('tag', 'one'), ('tag', 'two')),
            headers=((b'content-type', b'application/x-www-form-urlencoded'),),
            body=b'vmid=200',
            path_params={},
        )
        result = asyncio.run(DefaultProxyWorkflow(proxmox=proxmox).execute(request))
        self.assertIs(result, upstream)
        proxmox.request.assert_awaited_once_with(
            method='POST',
            path=request.raw_api_path,
            query_string=request.raw_query_string,
            headers=request.headers,
            body=request.body,
        )

    def test_adapter_can_be_used_directly_without_gateway_or_workflow(self):
        upstream = asyncio.run(self.client.request('GET', '/forbidden'))
        self.assertIsInstance(upstream, UpstreamResponse)
        self.assertEqual(upstream.status_code, 403)
        self.assertEqual(upstream.body, COMPRESSED_PAYLOAD)
        self.assertEqual(dict(upstream.headers)[b'content-type'], b'application/json')
        self.assertEqual(self.server.captured[-1]['path'], '/api2/json/forbidden')

    def test_adapter_returns_transport_errors_without_http_framework_types(self):
        for error, expected_error in (
            (requests.ConnectionError('private detail'), ProxmoxTransportError),
            (requests.Timeout('private detail'), ProxmoxTimeoutError),
        ):
            with self.subTest(error=expected_error.__name__), patch(
                'requests.Session.request', side_effect=error,
            ):
                with self.assertRaises(expected_error) as raised:
                    asyncio.run(self.client.request('GET', '/nodes'))
                self.assertNotIn('private detail', str(raised.exception))

    def test_custom_and_fallback_routing(self):
        matcher = RouteMatcher()
        routes = read_routes().routes
        custom = matcher.match(routes, 'POST', '/nodes/pve1/qemu/100/clone')
        self.assertEqual(custom.workflow, 'vm.clone')
        self.assertEqual(custom.path_params, {'node': 'pve1', 'vmid': '100'})
        fallback = matcher.match(routes, 'GET', '/nodes/pve1/qemu/100/clone')
        self.assertEqual(fallback.workflow, 'default_proxy')
        self.assertEqual(fallback.path_params, {})

    def test_default_proxy_is_selected_only_after_all_routes_are_checked(self):
        routes = [
            Route(id='first', endpoint='/version', method='GET', workflow='version'),
            *read_routes().routes,
        ]
        matcher = RouteMatcher()
        matched = matcher.match(routes, 'POST', '/nodes/pve1/qemu/100/clone')
        self.assertEqual(matched.workflow, 'vm.clone')

        for candidates, method, path in (
            (routes, 'POST', '/nodes/pve1/qemu/100/config'),
            (routes, 'POST', '/nodes/pve1/qemu/100/clone/extra'),
            ([], 'GET', '/nodes'),
        ):
            with self.subTest(path=path):
                fallback = matcher.match(candidates, method, path)
                self.assertEqual(fallback.route_id, 'default_proxy')
                self.assertEqual(fallback.workflow, 'default_proxy')
                self.assertEqual(fallback.path_params, {})

    def test_body_encoded_path_query_auth_and_response_are_forwarded(self):
        path = '/api2/json/nodes/pve1/tasks/UPID%3Apve1%3A/status?tag=one&tag=two&name=a%2Bb+z'
        body = b'newid=200&name=lab%2B1'
        status, headers, response_body = asyncio.run(call_gateway(
            'POST', path, body,
            headers=(
                (b'authorization', b'Bearer client-token'),
                (b'cookie', b'PVEAuthCookie=client-cookie'),
                (b'csrfpreventiontoken', b'client-csrf'),
                (b'content-type', b'application/x-www-form-urlencoded'),
                (b'connection', b'keep-alive, X-Client-Hop'),
                (b'x-client-hop', b'must-not-be-forwarded'),
            ),
        ))
        captured = self.server.captured[-1]
        self.assertEqual(captured['method'], 'POST')
        self.assertEqual(captured['path'], path)
        self.assertEqual(captured['body'], body)
        self.assertEqual(captured['headers']['Authorization'], self.settings.pve_authorization)
        for name in ('Cookie', 'CSRFPreventionToken', 'X-Client-Hop'):
            self.assertIsNone(captured['headers'].get(name))
        self.assertEqual(captured['headers']['Content-Type'], 'application/x-www-form-urlencoded')
        self.assertNotEqual(captured['headers']['Host'], 'gateway.test')
        self.assertEqual(status, 200)
        self.assertEqual(response_body, COMPRESSED_PAYLOAD)
        self.assertEqual(dict(headers)[b'content-encoding'], b'gzip')
        self.assertNotIn(b'connection', dict(headers))
        self.assertNotIn(b'x-internal', dict(headers))
        self.assertEqual([value for name, value in headers if name == b'set-cookie'], [b'one=1', b'two=2'])

    def test_upstream_error_status_is_preserved(self):
        status, _, response_body = asyncio.run(call_gateway('GET', '/api2/json/forbidden'))
        self.assertEqual(status, 403)
        self.assertEqual(response_body, COMPRESSED_PAYLOAD)

    def test_redirect_is_returned_without_following(self):
        status, headers, body = asyncio.run(call_gateway('GET', '/api2/json/redirect'))
        self.assertEqual(status, 302)
        self.assertEqual(dict(headers)[b'location'], b'/api2/json/nodes')
        self.assertEqual(body, b'')
        self.assertEqual(len(self.server.captured), 1)

    def test_head_preserves_content_length_without_body(self):
        status, headers, body = asyncio.run(call_gateway('HEAD', '/api2/json/nodes'))
        self.assertEqual(status, 200)
        self.assertEqual(dict(headers)[b'content-length'], str(len(COMPRESSED_PAYLOAD)).encode())
        self.assertEqual(body, b'')

    def test_api_root_retains_its_original_trailing_slash(self):
        for path in ('/api2/json', '/api2/json/'):
            with self.subTest(path=path):
                asyncio.run(call_gateway('GET', path))
                self.assertEqual(self.server.captured[-1]['path'], path)

    def test_clone_interceptor_does_not_fall_through_to_proxy(self):
        asyncio.run(call_gateway('POST', '/api2/json/nodes/pve1/qemu/100/clone'))
        self.assertEqual(self.server.captured, [])

    def test_transport_errors_do_not_leak_credentials(self):
        for error, expected_status in (
            (requests.ConnectionError('private transport detail'), 502),
            (requests.Timeout('private transport detail'), 504),
        ):
            with self.subTest(status=expected_status), patch(
                'requests.Session.request', side_effect=error,
            ):
                status, _, body = asyncio.run(call_gateway('GET', '/api2/json/nodes'))
                self.assertEqual(status, expected_status)
                self.assertNotIn(b'private transport detail', body)
                self.assertNotIn(b'fake-secret', body)

    def test_upstream_base_url_defaults_and_explicit_port(self):
        self.assertEqual(
            replace(self.settings, pve_api_host='pve.test').pve_api_base_url,
            'https://pve.test:8006/api2/json',
        )
        self.assertEqual(
            replace(self.settings, pve_api_host='https://pve.test:8443/api2/json/').pve_api_base_url,
            'https://pve.test:8443/api2/json',
        )


if __name__ == '__main__':
    unittest.main()
