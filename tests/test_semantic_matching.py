"""Assignment must follow actual source meaning, with no positional fallback."""
import copy
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

from app.core import semantic_match as matcher, semantic_review as review, workflow, ai


class Scores:
    def __init__(self, pairs, back=None):self.pairs=pairs;self.back=back or {}
    def score(self, ja, vi, base_ja, base_vi):
        return np.array([[self.pairs.get((j,v),-12.) for j in ja] for v in vi]),self.back


def region(rid,ja,order=0,bubble='round'):
    r=workflow.new_region(rid,[order*100,0,order*100+80,150])
    r.update(ja=ja,ja_src='ocr',order=order,bubble=bubble)
    return r


class AssignmentTests(unittest.TestCase):
    def test_two_translation_units_can_fill_three_connected_lobes(self):
        a, b, c = 'それにしてもよいのですか', '魔族である私の事でしたら気になさらずに', 'お受けになってもよろしいのに'
        first, second = 'Mà như thế có được không?', 'Nếu vì em là ma tộc thì chàng không cần bận tâm đâu.'
        lines = [f'{first} {second}', 'Chàng cứ nhận lời cũng được mà.']
        regs = [region(rid, ja, i) for i, (rid, ja) in enumerate(zip('ABC', (a, b, c)))]
        for reg in regs:
            reg['balloon'] = {'parts': 3, 'peers': list('ABC')}
        scores = Scores({(f'{a} {b}', lines[0]): 4, (c, lines[1]): 4, (a, first): 3, (b, second): 3,
                         (a, first + ' Nếu vì'): 3.1, (b, second.removeprefix('Nếu vì ')): 3.1})
        out = matcher.match(regs, lines, scores)
        self.assertEqual([(piece['id'], piece['text']) for piece in out['splits'][0]], [('A', first + ' '), ('B', second)])
        self.assertEqual(out['assign'][1][0], 'C')
        self.assertFalse(out['unplaced'])

    def test_recheck_joined_lobes_only_when_both_fragments_fit_their_source(self):
        a, b = '普通の人族がいくら鍛錬を繰り返しても', '到達することができない程の能力'
        first, second = 'Nhân tộc bình thường có khổ luyện bao nhiêu đi nữa', 'cũng chẳng thể sánh được…'
        line = f'{first} {second}'
        regs = [region('A', a, 1), region('B', b, 2)]
        for r in regs:
            r['balloon'] = {'peers': ['A', 'B'], 'parts': 2}
        result = {'assign': {0: ('A', None, '')}, 'splits': {}, 'evidence': {0: {'sources': ['A']}}}
        scores = {(a, line): 3.0, (f'{a} {b}', line): 3.4, (a, first): 2.4, (b, second): 1.9}
        ambiguous = copy.deepcopy(result)
        matcher.refine_joined_units(regs, [line], ambiguous, Scores({**scores, (a, second): 1.8}))
        self.assertFalse(ambiguous['splits'])
        matcher.refine_joined_units(regs, [line], result, Scores(scores))
        self.assertEqual([(p['id'], p['text']) for p in result['splits'][0]], [('A', first + ' '), ('B', second)])

    def test_joined_exclamation_can_move_to_its_own_lobe_despite_region_order(self):
        a, b = '遠い国から来たんだな', 'っは!'
        first, second = 'Ha!', 'Anh đến từ nơi xa thật đấy.'
        line = f'{first} {second}'
        regs = [region('A', a, 1), region('B', b, 2)]
        for r in regs:
            r['balloon'] = {'peers': ['A', 'B'], 'parts': 2}
        result = {'assign': {0: ('A', None, '')}, 'splits': {}, 'evidence': {0: {'sources': ['A']}}}
        scores = {(a, line): 2.99, (f'{b} {a}', line): 3.30, (b, first): 2.9, (a, second): 2.8}
        matcher.refine_joined_units(regs, [line], result, Scores(scores))
        self.assertEqual([(p['id'], p['text']) for p in result['splits'][0]], [('B', first + ' '), ('A', second)])

    def test_stylized_sentence_can_be_matched_even_with_an_effect_label(self):
        phrase = '狩りのノルマがーっ!!!'
        line = 'Chỉ tiêu đi săn của tôi!!!'
        result = matcher.match([region('A', phrase, bubble='sfx')], [line], Scores({(phrase, line): 3.0}))
        self.assertEqual(result['assign'][0][0], 'A')
        self.assertFalse(matcher.japanese_phrase('ゴゴゴゴゴゴゴ'))

    def test_shuffled_inputs_and_arbitrary_lengths_follow_meaning(self):
        a,b='今日は本当に楽しかったです','明日は一緒に行きましょう'
        lines=['Ngày mai cùng đi nhé.','Hôm nay thật vui.']
        regs=[region('A',a,1),region('B',b,2)]
        out=matcher.match(regs,lines,Scores({(a,lines[1]):4,(b,lines[0]):4}))
        self.assertEqual({k:v[0] for k,v in out['assign'].items()},{0:'B',1:'A'})
        self.assertFalse(out['unplaced'])

    def test_missing_source_is_unplaced_without_shifting_following_units(self):
        ja='明日は一緒に行きましょう';lines=['Một câu không có trong ảnh.','Ngày mai cùng đi nhé.']
        out=matcher.match([region('B',ja)],lines,Scores({(ja,lines[1]):4}))
        self.assertEqual(out['unplaced'],[0]);self.assertEqual(out['assign'][1][0],'B')

    def test_punctuation_does_not_take_a_dialogue_and_duplicate_sfx_are_preserved(self):
        regs=[region('A','もちろん大丈夫だよ'),region('B','!?',1,'sfx'),region('C','ドキ',2,'sfx'),region('D','ドキ',3,'sfx')]
        lines=['!?','Tất nhiên rồi!','thịch','thịch']
        out=matcher.match(regs,lines,Scores({('もちろん大丈夫だよ',lines[1]):4,('もちろん大丈夫だよ','!?'):50}))
        self.assertEqual(out['assign'][0][0],'B');self.assertEqual(out['assign'][1][0],'A')
        self.assertEqual({out['assign'][i][0] for i in (2,3)},{'C','D'})
        self.assertFalse(out['unplaced'])

    def test_one_kana_sfx_cannot_match_a_syllable_inside_unrelated_dialogue(self):
        out=matcher.match([region('A','リョータ様はリョータ様ですから')],['lững lờ~~'],Scores({}))
        self.assertEqual(out['unplaced'],[0]);self.assertFalse(out['assign'])

    def test_two_sound_units_fit_a_source_with_two_actual_repetitions(self):
        out=matcher.match([region('A','ドキドキ',bubble='sfx')],['thịch','thịch'],Scores({}))
        self.assertEqual([out['assign'][i][0] for i in (0,1)],['A','A'])
        single=matcher.match([region('A','ドキ',bubble='sfx')],['thịch','thịch'],Scores({}))
        self.assertEqual(len(single['assign']),1);self.assertEqual(len(single['unplaced']),1)

    def test_name_anchor_uses_actual_kana_reading_in_source_evidence(self):
        r=region('A','将司!');r['ja_variants']=['まさし 将司！']
        out=matcher.match([r],['Masashi!'],Scores({}))
        self.assertEqual(out['assign'][0][0],'A')
        wrong=matcher.match([r],['Ryouta!'],Scores({}))
        self.assertEqual(wrong['unplaced'],[0])

    def test_combined_word_sentence_can_split_across_unconnected_sources_by_meaning(self):
        a,b='今日は天気が悪いので','行けません'
        text='Hôm nay trời xấu, nên tôi không thể đi được.'
        prefix='Hôm nay trời xấu, ';tail='nên tôi không thể đi được.'
        scores={(a,text):4,(a,prefix):4,(b,tail):4}
        result=matcher.match([region('A',a),region('B',b,1)],[text],Scores(scores))
        self.assertEqual([p['id'] for p in result['splits'][0]],['A','B'])
        self.assertEqual(''.join(p['text'] for p in result['splits'][0]),text)

    def test_neighboring_units_cannot_merge_just_because_topic_is_similar(self):
        a='今日は一緒に遊んで楽しかったです';b='明日は学校へ行きましょう'
        lines=['Hôm nay rất vui.','Mai đi học nhé.']
        scores={(a,lines[0]):3,(a,lines[1]):1,(b,lines[1]):4,(a,' '.join(lines)):9}
        out=matcher.match([region('A',a),region('B',b,1)],lines,Scores(scores))
        self.assertEqual({k:v[0] for k,v in out['assign'].items()},{0:'A',1:'B'})

    def test_one_source_can_contain_acknowledgement_and_body(self):
        ja='おうよ!ものは試しでやってみたら恋人ができたよ!'
        lines=['Ừ!','Tao thử và đã có bạn gái!']
        scores={(ja,lines[1]):4,(ja,' '.join(lines)):4}
        out=matcher.match([region('A',ja)],lines,Scores(scores))
        self.assertEqual([out['assign'][i][0] for i in (0,1)],['A','A'])

    def test_connected_function_fragments_keep_target_grammar(self):
        regs=[region('A','デート'),region('B','ですから',1)]
        for r in regs:r['balloon']={'peers':['A','B']}
        lines=['Thì…','Chúng mình đang đi hẹn hò mà.']
        scores={('デート',lines[1]):4,('ですから',lines[0]):4,('デート ですから',' '.join(lines)):5}
        out=matcher.match(regs,lines,Scores(scores))
        self.assertEqual([out['assign'][i][0] for i in (0,1)],['A','B'])

    def test_word_split_is_scored_by_meaning_and_preserves_all_characters(self):
        text='Lilia... chủ động hơn mình nghĩ…'
        scores={('リリアって','Lilia...'):5,('思ったより積極的','chủ động hơn mình nghĩ…'):5}
        parts=matcher.split_unit(text,[region('A','リリアって'),region('B','思ったより積極的')],Scores(scores))
        self.assertEqual(''.join(p['text'] for p in parts),text)
        self.assertEqual(parts[0]['text'],'Lilia... ')

    def test_intervening_sfx_does_not_break_a_connected_balloon_pair(self):
        regs=[region('A','まずは登録'),region('B','ニックネームはリョータ',1)]
        for r in regs:r['balloon']={'peers':['A','B']}
        lines=['Đăng ký tài khoản…','pi','Biệt danh là Ryouta.']
        scores={('まずは登録',lines[0]):4,('ニックネームはリョータ',lines[2]):4,
                ('まずは登録 ニックネームはリョータ',' '.join((lines[0],lines[2]))):5}
        out=matcher.match(regs,lines,Scores(scores))
        self.assertEqual({k:v[0] for k,v in out['assign'].items()},{0:'A',2:'B'})
        self.assertEqual(out['unplaced'],[1])

    def test_profile_lobe_can_receive_name_and_year_without_losing_neighbor_context(self):
        regs=[region('A','山田太郎大学一年生'),region('B','この春大学に入学した',1)]
        for r in regs:r['balloon']={'peers':['A','B']}
        lines=['Tên tôi là Yamada Tarou.','Tôi là sinh viên năm nhất.']
        out=matcher.match(regs,lines,Scores({(regs[0]['ja'],lines[0]):1,(regs[0]['ja'],lines[1]):4}))
        self.assertEqual([out['assign'][i][0] for i in (0,1)],['A','A'])

    def test_bad_joint_translation_cannot_drop_two_clear_individual_meaning_pairs(self):
        regs=[region('A','名前を呼んでください'),region('B','さん付けは他人行儀なので',1)]
        for r in regs:r['balloon']={'peers':['A','B']}
        lines=['Cứ gọi tên em.','Gọi kèm san thì xa cách.']
        out=matcher.match(regs,lines,Scores({(regs[0]['ja'],lines[0]):4,(regs[1]['ja'],lines[1]):4}))
        self.assertEqual([out['assign'][i][0] for i in (0,1)],['A','B'])

    def test_high_joint_score_cannot_rescue_a_weak_lobe_with_a_better_separate_source(self):
        a, b, c = '明日は学校へ行く', '学校で勉強します', '学校は今日休みです'
        regs = [region('A', a, 1), region('B', b, 2), region('C', c, 3)]
        duplicate = '明日は学校へ行って勉強します'
        for r in regs[:2]:
            r['balloon'] = {'peers': ['A', 'B'], 'parts': 2}
            r['ja_variants'] = [duplicate]
        regs[2]['balloon'] = {'parts': 1, 'uncertain': True}
        lines = ['Hôm nay trường nghỉ.', 'Tôi sẽ học ở trường.']
        out = matcher.match(regs, lines, Scores({(a, lines[0]): .2, (b, lines[1]): 3,
                                                  (duplicate, lines[0]): 4,
                                                  (c, lines[0]): 2, (f'{a} {b}', ' '.join(lines)): 8}))
        self.assertEqual([out['assign'][i][0] for i in (0, 1)], ['C', 'B'])

    def test_dense_pages_have_bounded_memory_and_no_duplicate_input(self):
        opts=[[{'mask':1<<i,'a':i,'k':1,'score':3.,'mapped':[[str(i)]]}] for i in range(22)]
        chosen=matcher.optimize(opts,22)
        self.assertEqual(len(chosen),22);self.assertEqual(len(set(gi for gi,oi in chosen)),22)

    def test_global_alternative_margin_includes_reassignment_of_other_units(self):
        options=[[{'mask':1,'score':5.},{'mask':2,'score':4.9}], [{'mask':1,'score':4.8}]]
        chosen,marginal,unused=matcher.optimize(options,2,details=True)
        self.assertAlmostEqual(sum(options[g][o]['score'] for g,o in chosen),9.7)
        self.assertAlmostEqual(marginal[0][0],5.)
        self.assertAlmostEqual(unused[0],4.9)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.project=workflow.Project(temp.name);self.project.sync_documents([{'id':1,'name':'001.png'}])
        self.page=self.project.pages[0];self.text='Lilia... chủ động hơn mình nghĩ…'
        self.page.update(lines_manual=True,lines=[self.text],regions=[region('A','リリアって'),region('B','思ったより積極的',1)])
        self.parts=[{'id':'A','text':'Lilia... ','start':0,'end':9}, {'id':'B','text':self.text[9:],'start':9,'end':len(self.text)}]
        self.page['assignment_units']=[{'index':0,'text':self.text,'parts':self.parts,'evidence':{'version':matcher.VERSION,'score':4.,'margin':2.,'ja':'リリアって 思ったより積極的'}}]
        for r,p in zip(self.page['regions'],self.parts):r.update(lines=[p['text']],lines_src='auto')
        workflow.imwrite(self.project.file(self.page,'raw.png'),np.full((160,200,3),255,np.uint8))

    def test_split_units_pass_conservation_and_stale_neighbor_edits_invalidate_review(self):
        self.assertTrue(review.valid_provenance(self.project,self.page))

        review.review_local_assignments(self.project,self.page)
        with patch.object(review,'local_models',side_effect=AssertionError('valid source-bound evidence')):
            review.require_verified(self.project,self.page)
        a,b=self.page['regions'];b['lines']=['Thoại bị đổi']
        self.assertFalse(review.valid_provenance(self.project,self.page))
        self.assertIsNone(review.current_review(self.project,self.page,a))

    def test_duplicate_or_missing_spans_cannot_be_approved(self):
        self.parts[1]['start']=8
        self.assertFalse(review.valid_provenance(self.project,self.page))
        review.review_local_assignments(self.project,self.page)
        self.assertTrue(all(r['meaning_review']['state']=='review' for r in self.page['regions']))

    def test_missing_translation_model_preserves_old_lines_and_never_guesses(self):
        before=copy.deepcopy(self.page['regions'])
        with patch('app.core.source_ocr.prepare',return_value=[]),patch('app.core.semantic_match.translator',side_effect=FileNotFoundError('missing')):
            with self.assertRaises(FileNotFoundError):workflow.match(self.project,self.page,ai.AIConfig(provider='none'))
        self.assertEqual(self.page['regions'],before)

    def test_independent_ocr_respects_manual_japanese_and_reuses_image_cache(self):
        from app.core import source_ocr
        from types import SimpleNamespace
        image=np.full((160,200,3),255,np.uint8)
        for r in self.page['regions']:r.update(ja_src='manual',ja_variants=['誤読'])
        fake=SimpleNamespace(detect=lambda im:[])
        with patch.object(source_ocr,'source_ocr',return_value=fake) as detector, \
             patch('app.core.ocr.reader',side_effect=AssertionError('manual source')):
            source_ocr.prepare(self.project,self.page,image)
            source_ocr.prepare(self.project,self.page,image)
        self.assertEqual(detector.call_count,1)
        self.assertEqual([r['ja'] for r in self.page['regions']],['リリアって','思ったより積極的'])
        self.assertTrue(all('ja_variants' not in r for r in self.page['regions']))

    def test_changed_raw_cannot_reuse_automatic_japanese_from_old_image(self):
        from app.core import source_ocr
        from types import SimpleNamespace
        self.page['regions']=self.page['regions'][:1]
        r=self.page['regions'][0];r.update(ja='古い文字です',ja_src='ocr',ja_image='different image',ja_variants=['別の古い文字'])
        r['short_ocr_box']=[2,list(r['box'])]
        fake=SimpleNamespace(detect=lambda im:[])
        with patch.object(source_ocr,'source_ocr',return_value=fake), \
             patch('app.core.ocr.reader',return_value=SimpleNamespace(read=lambda im,b:'新しい文字です')):
            source_ocr.prepare(self.project,self.page,np.full((160,200,3),255,np.uint8))
        self.assertEqual(r['ja'],'新しい文字です')
        self.assertNotIn('別の古い文字',r['ja_variants'])

    def test_locking_one_split_fragment_does_not_duplicate_the_original_sentence(self):
        self.page['regions'][0]['lines_src']='manual'
        before=[list(r['lines']) for r in self.page['regions']]
        with patch('app.core.source_ocr.prepare',side_effect=AssertionError('no remaining input')), \
             patch('app.core.semantic_match.Scorer',side_effect=AssertionError('no remaining input')), \
             patch.object(review,'verify_page',return_value=['A','B']):
            workflow.match(self.project,self.page,ai.AIConfig(provider='none'))
        self.assertEqual([r['lines'] for r in self.page['regions']],before)
        self.assertFalse(self.page['unplaced'])
        self.assertTrue(review.valid_provenance(self.project,self.page))


class SourceModelInputTests(unittest.TestCase):
    def test_expanded_caption_discards_ocr_and_clean_state_from_the_old_crop(self):
        import hashlib
        from types import SimpleNamespace
        from app.core import source_ocr
        with tempfile.TemporaryDirectory() as folder:
            p=workflow.Project(folder);p.sync_documents([{'name':'001.png','id':1}]);page=p.pages[0]
            image=np.full((100,200,3),255,np.uint8)
            r=region('A','古い文字です');r.update(box=[10,10,130,40],cleaned=True,ja_variants=['別の古い文字'])
            page['regions']=[r]
            page['source_evidence']={'version':7,'image':hashlib.sha256(image.tobytes()).hexdigest(),'readings':[
                {'box':[10,10,130,70],'polygon':[[10,10],[130,10],[130,70],[10,70]],
                 'text':'マッチングしました','confidence':.95,'continuation':True}]}
            with patch('app.core.ocr.reader',return_value=SimpleNamespace(read=lambda *a:'マッチングしました')):
                source_ocr.prepare(p,page,image)
            self.assertEqual(r['box'],[10,10,130,70]);self.assertFalse(r['cleaned'])
            self.assertEqual(r['ja'],'マッチングしました')
            self.assertNotIn('別の古い文字',r['ja_variants'])

    def test_caption_verb_ending_joins_only_geometrically_aligned_lines(self):
        from app.core.source_ocr import caption_continuations
        readings=[{'polygon':[[0,0],[120,0],[120,20],[0,20]],'text':'マッチング','confidence':.99},
                  {'polygon':[[0,30],[120,30],[120,50],[0,50]],'text':'しました','confidence':.99}]
        merged=caption_continuations(readings)
        self.assertEqual(merged[0]['text'],'マッチングしました')
        self.assertEqual(merged[0]['anchor_box'],[0,0,120,20])
        readings[1]['polygon']=[[200,30],[320,30],[320,50],[200,50]]
        self.assertFalse(caption_continuations(readings))

    def test_retrieving_joint_context_also_evaluates_its_members(self):
        from types import SimpleNamespace
        a,b='さん付けは他人行儀','名前はリリア'
        ja=[a+' '+b]+[f'無関係な文章{i}です' for i in range(20)]+[a,b]
        seen=[]
        def evidence(source,target,selected):
            seen.append(selected.copy());return np.zeros((len(target),len(source),2))
        mt=SimpleNamespace(cache={},translations={},evidence=evidence,translate=lambda vi,*args:['']*len(vi))
        emb=SimpleNamespace(compare=lambda ja,vi:{k:np.asarray([[1.-i*.02 for i in range(len(ja))]]) for k in ('e5','minilm')})
        scorer=matcher.Scorer(translation=mt,embeddings=emb)
        scorer.score(ja,['Gọi kèm san thì xa cách.'],set(ja),{'Gọi kèm san thì xa cách.'})
        self.assertTrue(seen[0][0,-1]);self.assertTrue(seen[0][0,-2])

    def test_unretrieved_pair_cannot_change_scores_when_cache_is_warm(self):
        from app.core.local_translation import Translator
        model=object.__new__(Translator)
        model.cache={('a','v'):(-1.,-2.),('b','v'):(-.1,-.2)}
        values=model.evidence(['a','b'],['v'],np.asarray([[True,False]]))
        self.assertTrue(np.allclose(values[0,1],[-30.,-30.]))
        self.assertTrue(np.allclose(values[0,0],[-1.,-2.]))

    def test_ppocr_v5_detector_uses_its_own_normalization(self):
        from app.core.source_ocr import SourceOCR
        from types import SimpleNamespace
        seen=[]
        def run(outputs,feed):
            seen.append(feed['x']);return [np.zeros((1,1,32,32),np.float32)]
        model=object.__new__(SourceOCR)
        model.det=SimpleNamespace(run=run,get_inputs=lambda:[SimpleNamespace(name='x')])
        self.assertEqual(model.detect(np.full((32,32,3),255,np.uint8)),[])
        self.assertTrue(np.allclose(seen[0],1.))

    def test_recognition_padding_preserves_training_width_and_normalized_zero(self):
        from app.core.source_ocr import SourceOCR
        from types import SimpleNamespace
        seen=[]
        def run(outputs,feed):
            seen.append(feed['x']);return [np.asarray([[[.1,.9],[.9,.1],[.1,.9]]],np.float32)]
        model=object.__new__(SourceOCR);model.characters=['','あ']
        model.rec=SimpleNamespace(run=run,get_inputs=lambda:[SimpleNamespace(name='x')])
        result=model.recognize([np.full((20,20,3),255,np.uint8)])
        self.assertEqual(result[0][0],'ああ')
        self.assertEqual(seen[0].shape,(1,3,48,320))
        self.assertTrue(np.allclose(seen[0][:,:,:,:48],1.))
        self.assertTrue(np.allclose(seen[0][:,:,:,48:],0.))


if __name__=='__main__':unittest.main()
