"""Offline tokenizer preparation checks with tiny synthetic pinned assets."""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from model.data.prepare_tokenizer import TokenizerPreparationError, prepare_tokenizer


class PrepareTokenizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.metadata = self.root/'metadata'
        self.metadata.mkdir()
        self.destination = self.root/'tokenizer'
        self.contents = {'config.json': b'{"model_type":"deberta-v2"}\n',
                         'tokenizer_config.json': b'{"do_lower_case":false}\n',
                         'spm.model': b'synthetic sentencepiece fixture'}
        revision = 'a'*40
        artifacts = []
        for name, data in self.contents.items():
            algorithm = 'sha256' if name == 'spm.model' else 'git-blob-sha1'
            digest = hashlib.sha256(data).hexdigest() if algorithm == 'sha256' else hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            artifacts.append({'path': name, 'size_bytes': len(data),
                              'digest': {'algorithm': algorithm, 'value': digest},
                              'url': f'https://huggingface.co/microsoft/mdeberta-v3-base/resolve/{revision}/{name}'})
            if name != 'spm.model':
                (self.metadata/('backbone-'+name)).write_bytes(data)
        self.lock = self.root/'lock.json'
        self.lock.write_text(json.dumps({'backbone': {'repo_id':'microsoft/mdeberta-v3-base',
                                                      'revision':revision, 'artifacts':artifacts}}))
        self.fetch_count = 0

    def fetch(self, url, path, size):
        self.fetch_count += 1
        self.assertTrue(url.endswith('/spm.model'))
        path.write_bytes(self.contents['spm.model'])

    def prepare(self, **kwargs):
        return prepare_tokenizer(self.destination, lock_path=self.lock,
                                 metadata_root=self.metadata, fetch=kwargs.get('fetch',self.fetch))

    def test_original_metadata_and_download_are_verified_without_changes(self):
        result = self.prepare()
        self.assertEqual(len(result['files']),3)
        self.assertEqual(self.fetch_count,1)
        for name,data in self.contents.items():
            self.assertEqual((self.destination/name).read_bytes(),data)
            self.assertFalse((self.destination/(name+'.part')).exists())
        self.assertEqual((self.metadata/'backbone-config.json').read_bytes(),self.contents['config.json'])

    def test_valid_existing_files_are_reused_without_fetch(self):
        self.prepare()
        def unexpected(*args): self.fail('existing tokenizer must not download again')
        result = self.prepare(fetch=unexpected)
        self.assertTrue(all(item['reused'] for item in result['files']))

    def test_corrupt_source_and_download_are_not_published(self):
        path=self.metadata/'backbone-config.json'
        path.write_bytes(b'corrupt source')
        with self.assertRaises(TokenizerPreparationError): self.prepare()
        self.assertFalse(self.destination.exists())
        path.write_bytes(self.contents['config.json'])
        def corrupt(url,partial,size): partial.write_bytes(b'corrupt download')
        with self.assertRaises(TokenizerPreparationError): self.prepare(fetch=corrupt)
        self.assertFalse((self.destination/'spm.model').exists())
        self.assertEqual((self.destination/'spm.model.part').read_bytes(),b'corrupt download')

    def test_existing_corrupt_final_file_is_preserved(self):
        self.destination.mkdir()
        path=self.destination/'spm.model'
        path.write_bytes(b'keep existing corrupt file')
        with self.assertRaises(TokenizerPreparationError): self.prepare()
        self.assertEqual(path.read_bytes(),b'keep existing corrupt file')
        self.assertEqual(self.fetch_count,0)

    def test_symlink_destination_or_artifact_is_rejected(self):
        target=self.root/'keep';target.mkdir()
        self.destination.symlink_to(target,target_is_directory=True)
        with self.assertRaises(TokenizerPreparationError): self.prepare()
        self.destination.unlink()
        self.destination.mkdir()
        target_file=target/'original';target_file.write_bytes(b'unchanged')
        (self.destination/'spm.model').symlink_to(target_file)
        with self.assertRaises(TokenizerPreparationError): self.prepare()
        self.assertEqual(target_file.read_bytes(),b'unchanged')


if __name__ == '__main__':
    unittest.main()
