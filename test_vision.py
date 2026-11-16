"""Offline contracts: access, pagination, output images, bounds and terminal cleanup."""

import base64
import importlib.util
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('knowledge_vision_tool', ROOT / 'tool.py')
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)


def png():
    b = io.BytesIO()
    Image.new('RGB', (240, 120), 'red').save(b, 'PNG')
    return b.getvalue()


def modules(**exports):
    return {name: SimpleNamespace(**value) for name, value in exports.items()}


def pptx(relationship=None, extra=None):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('ppt/presentation.xml', '<presentation/>')
        if relationship:
            z.writestr('ppt/_rels/presentation.xml.rels', relationship)
        if extra:
            z.writestr(extra, b'fixture')
    return b.getvalue()


class VisionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tool = v.Tools()
        self.user = SimpleNamespace(id='reader', role='user')
        self.file = SimpleNamespace(path='server-owned', filename='fixture.png', hash='abc', updated_at=1)
        self.auth = AsyncMock(return_value=(self.user, self.file))
        self.source = AsyncMock(return_value=png())

    async def test_structured_image_output_and_image_no_terminal(self):
        with patch.object(v, '_authorize', self.auth), patch.object(v, '_source', self.source):
            result = await self.tool.read_knowledge_visual('kb', 'file', __user__={'id': 'reader'})
        self.assertEqual(result['total_pages'], 1)
        self.assertEqual(self.auth.await_count, 2)
        self.assertIsNone(result['next_page'])
        # Standalone image contract: the embedded image is decodable pixel data.
        # Native middleware extraction was separately exercised in the WebUI sandbox.
        self.assertEqual(len(result['pages']), 1)
        image = result['pages'][0]['image']
        self.assertTrue(image.startswith('data:image/jpeg;base64,'))
        pixels = Image.open(io.BytesIO(base64.b64decode(image.split(',')[1])))
        self.assertGreater(pixels.getpixel((20, 20))[0], 240)

    async def test_info_and_bounds(self):
        with patch.object(v, '_authorize', self.auth), patch.object(v, '_source', self.source):
            result = await self.tool.inspect_knowledge_visual('kb', 'file')
            self.assertEqual(result['pages'], [])
            for kwargs in (
                {'page_count': 0},
                {'page_count': 4},
                {'start_page': 0},
                {'start_page': True},
                {'start_page': 2},
            ):
                self.assertEqual((await self.tool.read_knowledge_visual('kb', 'file', **kwargs))['status'], 'error')
            self.assertEqual((await self.tool.read_knowledge_visual('../kb', 'file'))['status'], 'error')

    async def test_access_revocation_and_file_change_release_no_images(self):
        for changed in (v.VisionError('revoked'), SimpleNamespace(**{**vars(self.file), 'hash': 'changed'})):
            responses = [(self.user, self.file), changed if isinstance(changed, Exception) else (self.user, changed)]
            with (
                patch.object(v, '_authorize', AsyncMock(side_effect=responses)),
                patch.object(v, '_source', self.source),
            ):
                result = await self.tool.read_knowledge_visual('kb', 'file')
                self.assertEqual(result['status'], 'error')
                self.assertNotIn('pages', result)

    async def test_shared_kb_access_and_membership_are_both_required(self):
        auth = AsyncMock(return_value=self.user)
        kb = SimpleNamespace(
            get_knowledge_by_id=AsyncMock(return_value=SimpleNamespace(id='kb')),
            check_access_by_user_id=AsyncMock(return_value=True),
            has_file=AsyncMock(return_value=True),
        )
        files = SimpleNamespace(get_file_by_id=AsyncMock(return_value=self.file))
        mods = modules(
            **{
                'open_webui.utils.auth': {'get_optional_verified_user_from_request': auth},
                'open_webui.models.knowledge': {'Knowledges': kb},
                'open_webui.models.files': {'Files': files},
            }
        )
        with patch.dict(sys.modules, mods):
            user, file = await v._authorize(object(), {'id': 'reader'}, 'kb', 'file')
            self.assertIs(file, self.file)
            for access, membership in ((False, True), (True, False)):
                kb.check_access_by_user_id.return_value = access
                kb.has_file.return_value = membership
                with self.assertRaises(v.VisionError):
                    await v._authorize(object(), {'id': 'reader'}, 'kb', 'file')
            with self.assertRaises(v.VisionError):
                await v._authorize(object(), {'id': 'someone-else'}, 'kb', 'file')

    async def test_original_byte_limit(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'image'
            path.write_bytes(png())
            mods = modules(
                **{'open_webui.storage.provider': {'Storage': SimpleNamespace(get_file=lambda key: str(path))}}
            )
            with patch.dict(sys.modules, mods):
                with self.assertRaises(v.VisionError):
                    await v._source(self.file, 2)
                self.assertEqual(await v._source(self.file, 10000), png())

    def test_archive_and_image_limits(self):
        self.assertEqual(v._kind(pptx()), 'pptx')
        for data in (
            pptx(
                '<Relationships><Relationship TargetMode="External" Target="https://invalid.example/x"/></Relationships>'
            ),
            pptx(extra='ppt/vbaProject.bin'),
            pptx(extra='ppt/embeddings/object.bin'),
        ):
            with self.assertRaises(v.VisionError):
                v._kind(data)
        data, info = v._image(png(), 100)
        self.assertEqual((info['rendered_width'], info['rendered_height']), (100, 50))
        self.assertLess(len(data), 10000)

    async def test_document_pagination(self):
        render = AsyncMock(return_value=(5, [{'page': 2}, {'page': 3}]))
        with (
            patch.object(v, '_authorize', self.auth),
            patch.object(v, '_source', AsyncMock(return_value=b'%PDF-fixture')),
            patch.object(self.tool, '_render', render),
        ):
            result = await self.tool.read_knowledge_visual('kb', 'file', start_page=2, page_count=2)
        self.assertEqual(result['next_page'], 4)
        self.assertEqual(result['total_pages'], 5)

    async def test_terminal_routing_cleanup_and_failure(self):
        for failure in (False, True):
            requests = []
            directory = None

            def handle(req):
                nonlocal directory
                requests.append(req)
                self.assertEqual(req.headers['X-User-Id'], 'reader')
                if req.url.path == '/files/upload':
                    if directory is None:
                        directory = req.url.params['directory']
                        return httpx.Response(200, json={'path': '/home/reader/' + directory + '/source.pdf'})
                    return httpx.Response(200, json={})
                if req.url.path == '/execute':
                    return httpx.Response(
                        200,
                        json={
                            'status': 'exited',
                            'exit_code': 0,
                            'output': [
                                {
                                    'type': 'stdout',
                                    'data': json.dumps(
                                        {'error': 'fixture failure'} if failure else {'total_pages': 2, 'pages': [2]}
                                    ),
                                }
                            ],
                        },
                    )
                if req.url.path == '/files/view':
                    return httpx.Response(200, content=png())
                if req.url.path == '/files/delete':
                    self.assertEqual(req.url.params['path'], '/home/reader/' + directory)
                    return httpx.Response(200, json={})
                raise AssertionError(req.url)

            client = httpx.AsyncClient

            def factory(**kwargs):
                return client(**kwargs, transport=httpx.MockTransport(handle))

            resolver = AsyncMock(return_value=({'url': 'https://terminal.example'}, {'X-User-Id': 'reader'}, {}))
            mods = modules(
                **{
                    'open_webui.utils.terminals': {
                        'resolve_terminal_connection': resolver,
                        'get_terminal_server_url': lambda c: c['url'],
                    }
                }
            )
            with patch.dict(sys.modules, mods), patch.object(v.httpx, 'AsyncClient', factory):
                if failure:
                    with self.assertRaises(v.VisionError):
                        await self.tool._render(
                            b'%PDF-fixture', 'pdf', 2, 1, object(), self.user, {'terminal_id': 'selected'}
                        )
                else:
                    total, images = await self.tool._render(
                        b'%PDF-fixture', 'pdf', 2, 1, object(), self.user, {'terminal_id': 'selected'}
                    )
                    self.assertEqual(total, 2)
                    self.assertEqual(images[0]['page'], 2)
            self.assertEqual(requests[-1].method, 'DELETE')

    async def test_missing_terminal_is_actionable(self):
        mods = modules(
            **{
                'open_webui.utils.terminals': {
                    'resolve_terminal_connection': AsyncMock(return_value=None),
                    'get_terminal_server_url': lambda c: c['url'],
                }
            }
        )
        with patch.dict(sys.modules, mods), self.assertRaisesRegex(v.VisionError, 'selected, authorized'):
            await self.tool._render(b'%PDF-', 'pdf', 1, 1, None, self.user, {})


class AdditionalSourceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tool = v.Tools()
        self.user = SimpleNamespace(id='reader', role='user')
        self.file = SimpleNamespace(
            path='stored-file', filename='upload.png', hash='abc', updated_at=1, user_id='reader'
        )

    async def test_attachment_owner_shared_access_and_denial(self):
        permission = AsyncMock(return_value=False)
        mods = modules(
            **{
                'open_webui.models.files': {'Files': SimpleNamespace(get_file_by_id=AsyncMock(return_value=self.file))},
                'open_webui.models.knowledge': {'Knowledges': SimpleNamespace()},
                'open_webui.utils.access_control.files': {'has_access_to_file': permission},
            }
        )
        with patch.dict(sys.modules, mods), patch.object(v, '_caller', AsyncMock(return_value=self.user)):
            await v._authorize(object(), {'id': 'reader'}, None, 'file')
            permission.assert_not_awaited()  # The caller owns this upload.
            self.file.user_id = 'another-owner'
            with self.assertRaises(v.VisionError):
                await v._authorize(object(), {'id': 'reader'}, None, 'file')
            permission.return_value = True
            await v._authorize(object(), {'id': 'reader'}, None, 'file')
            permission.assert_awaited_with('file', 'read', self.user)

    async def test_attachment_read_info_and_document_pagination(self):
        auth = AsyncMock(return_value=(self.user, self.file))
        with patch.object(v, '_authorize', auth), patch.object(v, '_source', AsyncMock(return_value=png())):
            result = await self.tool.read_attachment_visual('file')
            self.assertEqual(result['source'], 'attachment')
            self.assertNotIn('knowledge_id', result)
            self.assertEqual(len(result['pages']), 1)
            self.assertIsNone(auth.call_args.args[2])
            self.assertEqual((await self.tool.inspect_attachment_visual('file'))['pages'], [])
            self.assertEqual((await self.tool.read_attachment_visual('file', page_count=4))['status'], 'error')
        with (
            patch.object(v, '_authorize', auth),
            patch.object(v, '_source', AsyncMock(return_value=b'%PDF-fixture')),
            patch.object(self.tool, '_render', AsyncMock(return_value=(5, [{'page': 2}, {'page': 3}]))),
        ):
            result = await self.tool.read_attachment_visual('file', start_page=2, page_count=2)
            self.assertEqual(result['next_page'], 4)

    async def test_terminal_stream_limits_encoding_and_no_redirects(self):
        path = '/tmp/special $(literal); filename.png'
        for response_code, limit, allowed in [
            (200, 10000, True),
            (200, 2, False),
            (403, 10000, False),
            (302, 10000, False),
        ]:
            requests = []

            def handle(req):
                requests.append(req)
                self.assertEqual(req.method, 'GET')
                self.assertEqual(req.url.path, '/files/view')
                self.assertEqual(req.url.params['path'], path)
                self.assertEqual(req.headers['X-User-Id'], 'reader')
                return httpx.Response(response_code, content=png(), headers={'Location': 'https://unrelated.invalid'})

            client = httpx.AsyncClient

            def factory(**kwargs):
                self.assertIs(kwargs['verify'], True)
                self.assertIs(kwargs['follow_redirects'], False)
                return client(**kwargs, transport=httpx.MockTransport(handle))

            connection = {'url': 'https://terminal.example'}
            binding = AsyncMock(return_value=(connection, {'X-User-Id': 'reader'}, {}))
            mods = modules(**{'open_webui.utils.terminals': {'get_terminal_server_url': lambda c: c['url']}})
            with (
                patch.dict(sys.modules, mods),
                patch.object(v, '_terminal_connection', binding),
                patch.object(v.httpx, 'AsyncClient', factory),
            ):
                if allowed:
                    data, selected = await v._terminal_source(
                        object(), self.user, {'terminal_id': 'selected'}, path, limit
                    )
                    self.assertEqual(data, png())
                    self.assertEqual(selected, connection)
                else:
                    with self.assertRaises(v.VisionError):
                        await v._terminal_source(object(), self.user, {'terminal_id': 'selected'}, path, limit)
            self.assertEqual(len(requests), 1)

    async def test_terminal_view_validates_paths_rechecks_connection_and_never_deletes_source(self):
        connection = {'id': 'selected'}
        caller = AsyncMock(return_value=self.user)
        source = AsyncMock(return_value=(png(), connection))
        binding = AsyncMock(return_value=(connection, {}, {}))
        with (
            patch.object(v, '_caller', caller),
            patch.object(v, '_terminal_source', source),
            patch.object(v, '_terminal_connection', binding),
        ):
            result = await self.tool.read_terminal_visual('/tmp/diagram.png')
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['source'], 'terminal')
            self.assertEqual(result['path'], '/tmp/diagram.png')
            self.assertEqual(len(result['pages']), 1)
            self.assertEqual(caller.await_count, 2)
            result = await self.tool.inspect_terminal_visual('/tmp/diagram.png')
            self.assertEqual(result['pages'], [])
            source.reset_mock()
            for bad in ('https://example.invalid/image.png', 'relative.png', '/tmp/null\x00.png'):
                self.assertEqual((await self.tool.read_terminal_visual(bad))['status'], 'error')
            source.assert_not_awaited()
            binding.side_effect = v.VisionError('access revoked')
            result = await self.tool.read_terminal_visual('/tmp/diagram.png')
            self.assertEqual(result['status'], 'error')
            self.assertNotIn('pages', result)
            binding.side_effect = None
            binding.return_value = ({'id': 'another-terminal'}, {}, {})
            self.assertEqual((await self.tool.read_terminal_visual('/tmp/diagram.png'))['status'], 'error')

    async def test_terminal_pdf_pagination(self):
        connection = {'id': 'selected'}
        with (
            patch.object(v, '_caller', AsyncMock(return_value=self.user)),
            patch.object(v, '_terminal_source', AsyncMock(return_value=(b'%PDF-fixture', connection))),
            patch.object(v, '_terminal_connection', AsyncMock(return_value=(connection, {}, {}))),
            patch.object(self.tool, '_render', AsyncMock(return_value=(5, [{'page': 4}, {'page': 5}]))),
        ):
            result = await self.tool.read_terminal_visual('/tmp/slides.pdf', start_page=4, page_count=2)
            self.assertEqual(result['status'], 'success')
            self.assertEqual(result['total_pages'], 5)
            self.assertIsNone(result['next_page'])


if __name__ == '__main__':
    unittest.main()
