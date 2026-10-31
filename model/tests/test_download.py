from pathlib import Path
import subprocess
import tempfile
import unittest

from model.data.download import DownloadError, download_dataset
from model.tests.helpers import change_lock, dataset_fixture, record


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        source=self.root/'source';source.mkdir()
        self.lock=dataset_fixture(source,{'train':[record()]})
        self.source=source
        self.dest=self.root/'dest'

    def fetch(self,url,path,size):
        name=url.split('/data/',1)[1]
        path.write_bytes((self.source/'data'/name).read_bytes())

    def test_downloads_and_verifies_all_pinned_files(self):
        result=download_dataset(self.lock,self.dest,fetch=self.fetch)
        self.assertEqual(len(result['files']),3)
        self.assertTrue(all(x['verified'] for x in result['files']))
        self.assertTrue((self.dest/'data/train.jsonl').is_file())

    def test_existing_verified_files_are_reused_without_network(self):
        download_dataset(self.lock,self.dest,fetch=self.fetch)
        def unexpected(*args): self.fail('network should not run')
        result=download_dataset(self.lock,self.dest,fetch=unexpected)
        self.assertTrue(all(x['reused'] for x in result['files']))

    def test_bad_digest_does_not_publish_file(self):
        def corrupt(url,path,size): path.write_bytes(b'corrupt')
        with self.assertRaises(DownloadError): download_dataset(self.lock,self.dest,fetch=corrupt)
        self.assertFalse((self.dest/'data/train.jsonl').exists())
        self.assertTrue((self.dest/'data/train.jsonl.part').exists())

    def test_existing_bad_file_is_not_overwritten(self):
        path=self.dest/'data/train.jsonl';path.parent.mkdir(parents=True);path.write_bytes(b'old')
        with self.assertRaises(DownloadError): download_dataset(self.lock,self.dest,fetch=self.fetch)
        self.assertEqual(path.read_bytes(),b'old')

    def test_interrupted_transfer_keeps_partial_for_resuming(self):
        def interrupted(url,path,size):
            path.write_bytes(b'partial')
            raise subprocess.CalledProcessError(28,['curl'])
        with self.assertRaises(DownloadError): download_dataset(self.lock,self.dest,fetch=interrupted)
        self.assertTrue((self.dest/'data/train.jsonl.part').exists())

    def test_partial_path_can_be_resumed_and_verified(self):
        path=self.dest/'data/train.jsonl.part';path.parent.mkdir(parents=True);path.write_bytes(b'partial')
        download_dataset(self.lock,self.dest,fetch=self.fetch)
        self.assertFalse(path.exists())
        self.assertEqual((self.dest/'data/train.jsonl').read_bytes(),(self.source/'data/train.jsonl').read_bytes())

    def test_unpinned_url_is_rejected_before_fetch(self):
        change_lock(self.lock,lambda x:x['dataset']['artifacts'][0].update(url='https://example.com/main/train.jsonl'))
        with self.assertRaises(DownloadError): download_dataset(self.lock,self.dest,fetch=self.fetch)
        self.assertFalse(self.dest.exists())

    def test_traversal_and_symlink_destinations_are_rejected(self):
        change_lock(self.lock,lambda x:x['dataset']['artifacts'][0].update(path='../escape.jsonl'))
        with self.assertRaises(DownloadError): download_dataset(self.lock,self.dest,fetch=self.fetch)

    def test_symlink_to_existing_dataset_is_not_followed(self):
        self.dest.symlink_to(self.source,target_is_directory=True)
        with self.assertRaises(DownloadError): download_dataset(self.lock,self.dest,fetch=self.fetch)

    def test_symlink_partial_cannot_modify_another_file(self):
        data=self.dest/'data';data.mkdir(parents=True)
        target=self.root/'keep.txt';target.write_bytes(b'keep')
        (data/'train.jsonl.part').symlink_to(target)
        with self.assertRaises(DownloadError): download_dataset(self.lock,self.dest,fetch=self.fetch)
        self.assertEqual(target.read_bytes(),b'keep')


if __name__=='__main__': unittest.main()
