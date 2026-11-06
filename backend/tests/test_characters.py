import base64
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from pydantic import ValidationError
from models.character_model import CharacterFields, CharacterUpdate, ReferenceUpload
from services import character_service as module
from services.image.qwen_image_service import QwenImageService


class FakeDB:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, collection, doc):
        self.docs[doc['_id']] = deepcopy(doc)

    async def find_one(self, collection, query):
        return deepcopy(self.docs.get(query['_id']))

    async def find_many(self, *args, **kwargs):
        return [deepcopy(d) for d in self.docs.values() if not d['archived']]

    async def replace_one(self, collection, query, doc):
        if self.docs[query['_id']]['revision'] != query['revision']:
            return 0
        self.docs[query['_id']] = deepcopy(doc)
        return 1

    async def update_one(self, collection, query, values):
        self.docs[query['_id']].update(values)
        return 1


def png():
    output = BytesIO()
    Image.new('RGB', (128, 128), 'red').save(output, format='PNG')
    return output.getvalue()


def immediate(coroutine_test):
    # These fake database operations complete immediately, without network or an event loop.
    def run(self):
        coroutine = coroutine_test(self)
        try:
            coroutine.send(None)
        except StopIteration:
            return
        finally:
            coroutine.close()
        raise AssertionError("Offline character test unexpectedly waited for external I/O")
    return run


class CharacterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent, ignore_cleanup_errors=True)
        self.root = Path(self.temp.name) / 'backend'
        self.db = FakeDB()
        self.patches = [patch.object(module, 'mongodb', self.db), patch.object(module, 'BACKEND_DIR', self.root)]
        for p in self.patches:
            p.start()
        self.service = module.CharacterService()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    @immediate
    async def test_revisions_references_and_frozen_profiles_survive_edits_archive(self):
        first = await self.service.create(CharacterFields(name='主角'))
        cid = first['character_id']
        anchor = await self.service.reference(cid, base64.b64encode(png()).decode())
        self.assertEqual(anchor['revision'], 2)
        confirmed = await self.service.confirm(cid)
        frozen = await self.service.get_confirmed_profiles([cid])
        new = await self.service.update(cid, CharacterUpdate(name='新名字'))
        self.assertFalse(new['confirmed'])
        self.assertEqual(new['revision'], 3)
        self.assertEqual(frozen[0], confirmed)
        self.assertEqual(self.db.docs[cid]['versions'][1], confirmed)
        self.assertTrue(module.local_asset(frozen[0]['reference_path']).is_file())
        with self.assertRaises(ValueError):
            await self.service.get_confirmed_profiles([cid])
        await self.service.archive(cid)
        with self.assertRaises(LookupError):
            await self.service.get(cid)
        self.assertTrue(module.local_asset(frozen[0]['reference_path']).is_file())

    @immediate
    async def test_each_reference_view_adds_an_unconfirmed_revision(self):
        profile = await self.service.create(CharacterFields(name="主角"))
        cid = profile["character_id"]
        encoded = base64.b64encode(png()).decode()
        side = await self.service.reference(cid, encoded, "profile")
        self.assertIsNone(side["reference_path"])
        with self.assertRaises(ValueError):
            await self.service.confirm(cid)
        front = await self.service.reference(cid, encoded)
        confirmed = await self.service.confirm(cid)
        self.assertEqual(confirmed["reference_images"]["front"], front["reference_path"])
        frozen = (await self.service.get_confirmed_profiles([cid]))[0]
        whole = await self.service.reference(cid, encoded, "whole")
        self.assertEqual(whole["revision"], confirmed["revision"] + 1)
        self.assertFalse(whole["confirmed"])
        self.assertEqual(whole["reference_images"]["profile"], side["reference_images"]["profile"])
        self.assertEqual(whole["reference_path"], front["reference_path"])
        self.assertNotIn("whole", frozen["reference_images"])
        self.assertTrue(self.db.docs[cid]["versions"][-2]["confirmed"])
        self.assertTrue(all(module.local_asset(path).is_file() for path in whole["reference_images"].values()))
        self.assertEqual(ReferenceUpload(image_base64=encoded).view, "front")
        with self.assertRaises(ValidationError):
            ReferenceUpload(image_base64=encoded, view="../outside")

    @immediate
    async def test_invalid_images_and_missing_confirm_are_rejected(self):
        profile = await self.service.create(CharacterFields(name='A'))
        cid = profile['character_id']
        with self.assertRaises(ValueError):
            await self.service.confirm(cid)
        for encoded in ['garbage', base64.b64encode(b'not an image').decode()]:
            with self.assertRaises(ValueError):
                await self.service.reference(cid, encoded)
        with self.assertRaises(ValueError):
            await self.service.get('../outside')
        with self.assertRaises(ValueError):
            await self.service.update(cid, CharacterUpdate(rate='nonsense'))
        self.assertEqual((await self.service.get(cid))['revision'], 1)

    @immediate
    async def test_reference_multimodal_content_and_model_guard(self):
        path = self.root / 'assets' / 'image.png'
        path.parent.mkdir(parents=True)
        path.write_bytes(png())
        provider = QwenImageService()
        content = provider._reference_content(['backend/assets/image.png'], 'qwen-image-2.0')
        self.assertTrue(content[0]['image'].startswith('data:image/png;base64,'))
        with self.assertRaises(ValueError):
            provider._reference_content(['backend/assets/image.png'], 'unknown')
        with self.assertRaises(ValueError):
            provider._reference_content(['https://example.com/a.png'], 'qwen-image-2.0')
        with self.assertRaises(ValueError):
            provider._reference_content(['backend/assets/image.png'] * 4, 'qwen-image-2.0')

    def test_generate_request_contains_images_then_text(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        import services.image.qwen_image_service as qwen
        source = self.root / 'assets' / 'image.png'
        source.parent.mkdir(parents=True)
        source.write_bytes(png())
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value.json.return_value = {'output': {'choices': [
            {'message': {'content': [{'image': 'https://example.com/result.png'}]}}]}}
        client.get.return_value.content = png()
        with patch.object(qwen, 'settings', SimpleNamespace(enable_paid_api=True)), \
             patch.object(qwen, 'model_config', return_value={'model': 'qwen-image-2.0', 'key': 'fake', 'url': 'https://example.com'}), \
             patch.object(qwen.httpx, 'Client', return_value=client), \
             patch.object(qwen, 'Path') as mock_path:
            mock_path.return_value.resolve.return_value.parents = [None, None, self.root]
            provider = QwenImageService()
            self.assertTrue(provider.supports_reference_images)
            provider.generate_image('test', 'scene', 'use reference', reference_images=['backend/assets/image.png'])
        payload = client.post.call_args.kwargs['json']
        content = payload['input']['messages'][0]['content']
        self.assertTrue(content[0]['image'].startswith('data:image/png;base64,'))
        self.assertEqual(content[1], {'text': 'use reference'})

    def test_client_cannot_set_confirmation_or_reference_path(self):
        for field in ['confirmed', 'reference_path', 'revision']:
            with self.assertRaises(ValidationError):
                CharacterFields(name='A', **{field: True})


if __name__ == '__main__':
    unittest.main()

