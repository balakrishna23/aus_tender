import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
import hybrid
from planning import rule_plan


def vector(position):
    result=[0.0]*384;result[position]=1.0;return result


class HybridTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'index.db'
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript('CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT); CREATE TABLE embeddings(hash TEXT PRIMARY KEY,text TEXT,vector BLOB);')
            connection.executemany('INSERT INTO metadata VALUES (?,?)',[('model',hybrid.MODEL),('model_digest','fixture-digest')])
            connection.commit()

    def tearDown(self):self.temp.cleanup()

    def cache(self,text,position):
        with closing(sqlite3.connect(self.path)) as connection:
            for part in hybrid.chunks(text):
                connection.execute('INSERT INTO embeddings VALUES (?,?,?)',(hybrid.key(part),part,hybrid.pack(vector(position))))
            connection.commit()

    def rank(self,rows,topic):
        return hybrid.rank(rows,topic,index=self.path,embedder=lambda _: [vector(0)],digest_provider=lambda:'fixture-digest')

    def test_semantic_synonym_can_enter_without_keyword_overlap(self):
        self.cache('Security breach investigation',0);self.cache('Office furniture delivery',1)
        rows=[{'cn_id':'CN1','description':'Security breach investigation'}, {'cn_id':'CN2','description':'Office furniture delivery'}]
        selected,status=self.rank(rows,'incident response')
        self.assertEqual(['CN1'],[r['cn_id'] for r in selected])
        self.assertEqual('semantic',selected[0]['retrieval']['method'])
        self.assertEqual(2,status['eligible_records'])

    def test_keyword_and_semantic_agreement_ranks_first(self):
        rows=[{'description':'incident response specialist'},{'description':'breach investigation'}]
        result=hybrid.fuse(rows,[r['description'] for r in rows],'incident response',[0.8,0.9])
        self.assertEqual('keyword + semantic',result[0]['retrieval']['method'])

    def test_generic_short_description_is_not_weak_semantic_match(self):
        selected=hybrid.fuse([{'description':'Security'}],['Security'],'breach investigation',[0.48])
        self.assertEqual([],selected)

    def test_one_generic_query_word_does_not_admit_lexical_result(self):
        selected=hybrid.fuse([{'description':'Cloud Services'}],['Cloud Services'],'cloud migration',[0.2])
        self.assertEqual([],selected)

    def test_new_text_does_not_use_old_vector(self):
        self.cache('Old description',0)
        with self.assertRaisesRegex(ValueError,'new/changed'):
            self.rank([{'description':'Changed description'}],'incident response')

    def test_model_change_requires_rebuild(self):
        self.cache('incident response',0)
        with self.assertRaisesRegex(ValueError,'differs'):
            hybrid.rank([{'description':'incident response'}],'incident response',index=self.path,
                        embedder=lambda _: [vector(0)],digest_provider=lambda:'new-digest')

    def test_missing_index_is_explicit(self):
        with self.assertRaisesRegex(ValueError,'missing'):
            hybrid.rank([{'description':'incident response'}],'incident response',index=self.path.parent/'absent.db')

    def test_chunk_overlap_and_short_topic(self):
        parts=hybrid.chunks(' '.join('w'+str(i) for i in range(300)))
        self.assertEqual(parts[0].split()[-20:],parts[1].split()[:20])
        with self.assertRaises(ValueError):hybrid.topic_for('question',{},'x'*301)

    def test_topic_does_not_embed_dates_or_prices(self):
        question='Which cyber contracts end within 90 days above $100k?'
        self.assertEqual('cyber',hybrid.topic_for(question,rule_plan(question)))
        self.assertEqual('breach investigation',hybrid.topic_for('Contracts about "breach investigation" ending within 30 days',{}))
        self.assertEqual('cloud migration',hybrid.topic_for('Contracts related to cloud migration ending within 30 days above $100k',{}))

    def test_model_invalid_vectors_fail_closed(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return json.dumps({'embeddings':[[0.0]*384]}).encode()
        with patch.object(hybrid,'urlopen',return_value=Response()):
            with self.assertRaisesRegex(ValueError,'Zero'):hybrid.embed(['sample'])


if __name__=='__main__':unittest.main()
