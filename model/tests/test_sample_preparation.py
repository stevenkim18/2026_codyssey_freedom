import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from model.data.prepare_sample import prepare_sample, SampleError


class Planner:
    max_span=12
    metadata={'splitter_id':'fixture','version':'1'}
    def tokens(self,text):return [(text,0,len(text))]
    def windows(self,text):return [(0,len(text))]


class SampleTests(unittest.TestCase):
    def make_source(self,root):
        rows=[]
        for i in range(40):
            text=f'synthetic{i}'
            rows.append({'doc_id':str(i),'domain':'a' if i<20 else 'b','text':text,
                         'entities':[{'type':'PERSON' if i else 'URL','start':0,'end':len(text),'surface':text}]})
        rows.append(dict(rows[1],doc_id='duplicate-text'))
        path=root/'train.jsonl'
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        return path,hashlib.sha256(path.read_bytes()).hexdigest()

    def test_unique_balanced_and_rare_type_preserved(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);path,digest=self.make_source(root)
            manifest=prepare_sample(path,root/'sample',Planner(),{'PERSON':'person','URL':'URL'},
                                    domains=['a','b'],count=10,seed=42,source_sha256=digest)
            self.assertEqual(manifest['summary']['unique_texts'],10)
            self.assertEqual(manifest['summary']['domains'],{'a':5,'b':5})
            self.assertGreater(manifest['summary']['entities_by_type']['URL'],0)
            self.assertEqual(manifest['source_scan']['duplicate_texts_excluded'],1)

    def test_reuse_requires_source_configuration_and_payload_hashes(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);path,digest=self.make_source(root)
            options=dict(domains=['a','b'],count=10,seed=42,source_sha256=digest)
            first=prepare_sample(path,root/'sample',Planner(),{'PERSON':'person','URL':'URL'},**options)
            self.assertEqual(first,prepare_sample(path,root/'sample',Planner(),{'PERSON':'person','URL':'URL'},**options))
            with self.assertRaises(SampleError):
                prepare_sample(path,root/'sample',Planner(),{'PERSON':'person','URL':'URL'},**dict(options,seed=43))
            (root/'sample/train.gliner.jsonl').write_text('changed')
            with self.assertRaises(SampleError):
                prepare_sample(path,root/'sample',Planner(),{'PERSON':'person','URL':'URL'},**options)

    def test_wrong_source_hash_does_not_create_output(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);path,digest=self.make_source(root)
            with self.assertRaises(SampleError):
                prepare_sample(path,root/'sample',Planner(),{'PERSON':'person','URL':'URL'},
                               domains=['a','b'],count=10,seed=42,source_sha256='0'*64)
            self.assertFalse((root/'sample').exists())
