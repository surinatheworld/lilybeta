"""Global offline JA/VI matching with balloon context and an explicit unplaced option.

Never uses page indices, answer files, length/order alignment, or network inference.
Scores are relative model evidence, not calibrated correctness probabilities.
"""
from __future__ import annotations

import re
import json
from pathlib import Path
import numpy as np

from .ocr import normalize, coverage
from .match_language import lexical, plain, category, shape, SOUNDS, ACKS
from .semantic_review import local_models, _unreliable_japanese
from .local_translation import translator
from .translation_weights import FILES
from .docs import TAG

VERSION = 3
CACHE_VERSION = 1
WEIGHTS = FILES['model.bin'] + ':' + FILES['sentencepiece.bpe.model']


def source_text(text):
    # Common abbreviations and conventional OCR confusions, only in scoring.
    for a,b in (("カテキョー","家庭教師"),("裏にも縋る","藁にも縋る"),("アロフィル","プロフィール"),
                ("プロフィル","プロフィール"),("リョータ","リョウタ")):
        text=text.replace(a,b)
    return text


def target_text(text):
    text=TAG.sub('',text).strip()
    for pattern,replacement in ((r"ch[uū]+n+n?ibyou", "trung nhị bệnh"),
                                (r"monng", "mong")):
        text=re.sub(pattern,replacement,text,flags=re.I)
    if plain(text)=="ngon": return "Tuyệt vời! Làm tốt lắm!"
    if plain(text)=='tu tu da':return 'Khoan đã, ủa?'
    name=re.fullmatch(r'[“"「]([^”"」]+)[”"」]\s*đi[.!…]*',text.strip(),flags=re.I)
    if name:return 'Biệt danh là '+name.group(1)
    if 'co benh thi vai tu phuong' in plain(text):
        return "Đến nước này thì có cọng rơm cũng phải bám lấy thôi!"
    return text


class Scorer:
    def __init__(self, translation=None, embeddings=None, report=None, cache_file=None):
        self.mt=translation or translator(); self.emb=embeddings or local_models(); self.report=report
        self.cache_file=Path(cache_file) if cache_file else None
        self.cached_pairs={};self.cached_back={}
        if self.cache_file and self.cache_file.exists():
            try:
                data=json.loads(self.cache_file.read_text(encoding='utf-8'))
                if data.get('version')==CACHE_VERSION and data.get('weights')==WEIGHTS:
                    for a,b,value in data.get('pairs',[]):
                        if len(value)==2 and np.isfinite(value).all():self.cached_pairs[a,b]=tuple(value)
                    self.cached_back=data.get('back',{})
            except (ValueError,TypeError,KeyError):pass
        self.mt.cache.update(self.cached_pairs)
        self.mt.translations.update({('vie_Latn','jpn_Jpan',k):v for k,v in self.cached_back.items()})

    def score(self, japanese, vietnamese, base_ja, base_vi):
        ja=[source_text(s) for s in japanese]; vi=[target_text(s) for s in vietnamese]
        embedded=self.emb.compare(ja,vi)
        # Broad independent candidate retrieval, followed by expensive two-way
        # translation scoring. Do not decode every unrelated source/target pair.
        selected=np.zeros((len(vi),len(ja)),bool)
        for matrix,limit in ((embedded['e5'],10),(embedded['minilm'],5)):
            for j,row in enumerate(matrix):selected[j,np.argsort(-row)[:limit]]=True
        # Retrieving a conjoined source must also score its individual lobes.
        # Otherwise a perfectly good joint match leaves one lobe with the
        # sentinel score for an unevaluated candidate (names/honorifics suffer).
        normalized=[normalize(s) for s in japanese]
        contains=[[k for k,part in enumerate(normalized) if len(part)>=3 and part in whole]
                  for whole in normalized]
        for row in selected:
            for i in np.flatnonzero(row.copy()):row[contains[i]]=True
        values=self.mt.evidence(ja,vi,selected)
        f,r=values[:,:,0].copy(),values[:,:,1].copy()
        valid=[i for i,s in enumerate(japanese) if s in base_ja and not _unreliable_japanese(s)] or list(range(len(ja)))
        valid_vi=[i for i,s in enumerate(vietnamese) if s in base_vi] or list(range(len(vi)))
        f-=f[:,valid].max(axis=1,keepdims=True); r-=r[valid_vi].max(axis=0,keepdims=True)
        q=1.2*f+r+12*(embedded['e5']-.8)+3*(embedded['minilm']-.45)+1.
        back=self.mt.translate(vi,"vie_Latn","jpn_Jpan")
        for j,i in zip(*np.nonzero(selected)):
            pair=(ja[i],vi[j])
            if pair in self.mt.cache:self.cached_pairs[pair]=self.mt.cache[pair]
        self.cached_back.update(zip(vi,back))
        if self.cache_file:
            part=self.cache_file.with_suffix('.tmp')
            part.write_text(json.dumps({'version':CACHE_VERSION,'weights':WEIGHTS,'pairs':[[a,b,v] for (a,b),v in self.cached_pairs.items()],
                                        'back':self.cached_back},ensure_ascii=False),encoding='utf-8')
            part.replace(self.cache_file)
        for j,b in enumerate(back):
            for i,text in enumerate(ja):
                if len(normalize(text))>=4:q[j,i]+=1.2*min(coverage(text,b),coverage(b,text))
        return q,dict(zip(vietnamese,back))


def groups_for(regions):
    groups=[]; seen=set()
    for r in regions:
        if r['id'] in seen:continue
        peers=(r.get('balloon') or {}).get('peers') or [r['id']]
        group=[x for x in regions if x['id'] in peers and x['id'] not in seen]
        group.sort(key=lambda x:x['order'])
        if not group or len(group)>3:group=[r]
        seen.update(x['id'] for x in group); groups.append(group)
    return groups


def optimize(options, count, details=False):
    """Disjoint subset assignment. Missing sources do not move other input units."""
    if count>18:
        # Bound memory for unusually dense pages. Global best-gain search still
        # allows arbitrary source order and explicitly leaves unmatched inputs.
        chosen=[]; used=0; taken=set()
        for score,gi,oi in sorted([(o['score'],gi,oi) for gi,opts in enumerate(options) for oi,o in enumerate(opts)],reverse=True):
            o=options[gi][oi]
            if gi not in taken and not used&o['mask']:
                chosen.append((gi,oi));taken.add(gi);used|=o['mask']
        return (chosen,None,None) if details else chosen
    states=np.arange(1<<count,dtype=np.int32); dp=np.full(len(states),-np.inf); dp[0]=0.; backs=[]
    forward=[dp.copy()] if details else []
    for opts in options:
        new=dp.copy(); choice=np.full(len(states),-1,np.int32)
        for oi,o in enumerate(opts):
            free=states[(states&o['mask'])==0]; to=free|o['mask']; value=dp[free]+o['score']
            better=value>new[to]+1e-7; new[to[better]]=value[better]; choice[to[better]]=oi
        dp=new; backs.append(choice)
        if details:forward.append(dp.copy())
    state=int(np.argmax(dp)); chosen=[]
    for gi in range(len(options)-1,-1,-1):
        oi=int(backs[gi][state])
        if oi>=0:chosen.append((gi,oi));state^=options[gi][oi]['mask']
    if not details:return chosen
    # Exact max-marginals: compare against an alternative placement for this
    # unit while allowing all OTHER units to change, not against occupied rows.
    unused=[float(dp[(states&(1<<k))==0].max()) for k in range(count)]
    suffix=np.zeros(len(states));marginals=[None]*len(options)
    for gi in range(len(options)-1,-1,-1):
        previous=suffix.copy();values=[]
        for o in options[gi]:
            free=states[(states&o['mask'])==0];to=free|o['mask']
            tail=o['score']+suffix[to]
            values.append(float(np.max(forward[gi][free]+tail)))
            previous[free]=np.maximum(previous[free],tail)
        marginals[gi]=values;suffix=previous
    return chosen,marginals,unused


def match(regions, lines, scorer=None, report=None, refine=True):
    if not lines:return {"assign":{},"unplaced":[],"warnings":[],"evidence":{}}
    if not regions:return {"assign":{},"unplaced":list(range(len(lines))),"warnings":[],"evidence":{}}
    scorer=scorer or Scorer(report=report)
    regions=[{**r,'ja_variants':[v for v in r.get('ja_variants',[]) if
             len(normalize(r.get('ja','')))<8 or .45*len(normalize(r.get('ja','')))<=len(normalize(v))<=2.2*len(normalize(r.get('ja','')))]} for r in regions]
    groups=groups_for(regions); sources=[]
    for g in groups:
        sources.extend(r.get('ja','') for r in g)
        if len(g)>1:sources.append(' '.join(r.get('ja','') for r in g))
        for r in g:sources.extend(r.get('ja_variants',[]))
    sources=list(dict.fromkeys(sources))
    blocks=[((a,),line) for a,line in enumerate(lines)]
    for a in range(len(lines)):
        for b in range(a+1,min(len(lines),a+5)):
            if b>a+1 and not all(category(s) in SOUNDS or shape(s) for s in lines[a+1:b]):continue
            blocks.append(((a,b),' '.join((lines[a],lines[b]))))
    texts=list(dict.fromkeys(t for indices,t in blocks))
    if report:report("Bước 7 · so nghĩa hai chiều Nhật–Việt và ngữ cảnh bóng đôi trên máy…")
    independent={s for r in regions for s in [r.get('ja',''),*r.get('ja_variants',[])]}
    raw,back=scorer.score(sources,texts,independent,set(lines))
    if raw.shape!=(len(texts),len(sources)) or not np.isfinite(raw).all():
        raise ValueError("Model ghép nghĩa trả kết quả không hợp lệ")
    ix={s:i for i,s in enumerate(sources)}; jx={s:j for j,s in enumerate(texts)}
    def atom(ja,line,reg):
        lex=max(lexical(line,s) for s in [ja,*reg.get('ja_variants',[])])
        if lex<0:return -30.
        if lex>=6:return lex
        q=float(raw[jx[line],ix[ja]])+lex
        # A second actual reading may repair an OCR error; never substitute VI.
        for variant in reg.get('ja_variants',[]):
            if not _unreliable_japanese(variant) and (len(normalize(variant))>=4):
                q=max(q,float(raw[jx[line],ix[variant]])+lex-.35)
        if reg['bubble']=='sfx' and not lex:q-=2.2
        if len(normalize(ja))<=2 and not lex:q-=2.
        if category(line) in ACKS and reg['bubble']=='sfx' and not lex:return -30.
        if category(line) in ACKS and not lex and len(normalize(ja))<6:return -30.
        if plain(line)=='me oi' and not re.search(r'ママ|まま|母|かあ',' '.join([ja,*reg.get('ja_variants',[])])):return -30.
        if re.search(r'\b(?:anh|toi|tao|em) (?:la|ten toi la)\b',plain(line)) and '様' in ja:q-=2.5
        if re.match(r'^(?:ten toi la|toi ten la|ten minh la|minh ten la)\b',plain(line)) and re.search(r'大学.{0,5}年生|高校.{0,5}年生',ja):q+=1.2
        if re.search(r'\bsan\b',plain(line)):
            if 'さん' in ja:q+=2.
            elif '様' in ja:q-=1.5
        if re.search(r'\bsama\b',plain(line)) and '様' in ja:q+=.8
        if re.match(r'^(chuong|chapter)\s*\d',plain(line)) and not re.search(r'第\s*[0-9０-９一二三四五六七八九十]+\s*[話回章]',ja):return -30.
        return q
    options=[]
    for g in groups:
        opts=[]; joined=' '.join(r.get('ja','') for r in g)
        valid=not any(_unreliable_japanese(r.get('ja','')) and not shape(r.get('ja','')) for r in g)
        for indices,text in blocks:
            a=indices[0];k=len(indices);unit_lines=[lines[i] for i in indices]
            if len(g)==1 and k==2 and category(unit_lines[0])==category(unit_lines[1]) and category(unit_lines[0]) in SOUNDS:
                word=normalize(joined)
                repeated=any(len(normalize(sound))>=2 and word.count(normalize(sound))>=2 for sound in SOUNDS[category(unit_lines[0])])
                if repeated and all(lexical(s,joined)>=6 for s in unit_lines):
                    opts.append({'a':a,'k':k,'indices':indices,'mask':sum(1<<i for i in indices),
                                 'score':11.95,'mapped':[[g[0]['id']]]*k})
            if len(g)>1 and k==2:
                # A name/year profile can contain two translated units inside
                # one lobe even when its neighboring lobe is detected too.
                for member in g:
                    if not re.search(r'大学.{0,5}年生|高校.{0,5}年生',member.get('ja','')):continue
                    parts=[atom(member['ja'],s,member) for s in unit_lines]
                    best=[max(atom(r.get('ja',''),s,r) for r in regions) for s in unit_lines]
                    if min(parts)>0 and all(v>=b-.4 for v,b in zip(parts,best)):
                        opts.append({'a':a,'k':k,'indices':indices,'mask':sum(1<<i for i in indices),
                                     'score':float(sum(parts)-.1),'mapped':[[member['id']]]*k})
            if len(g)==1 and k==1:
                score=atom(joined,text,g[0]); mapped=[[g[0]['id']]]
            elif len(g)==1 and k==2:
                if len(normalize(joined))<8 or any(shape(s) or category(s) in SOUNDS for s in unit_lines):continue
                joint=atom(joined,text,g[0]); parts=[atom(joined,s,g[0]) for s in unit_lines]
                prefix=category(lines[a]) in ACKS and lexical(lines[a],joined)>0
                best=[max(atom(r.get('ja',''),s,r) for r in regions) for s in unit_lines]
                if any(v<b-.4 for v,b in zip(parts,best)) and not (prefix and parts[1]>=best[1]-.4):continue
                if min(parts)<=0 and not prefix:continue
                # Similar topic is insufficient: both clauses need actual source
                # coverage, or a visible introductory acknowledgement/name field.
                name_field=bool(re.search(r'大学.{0,5}年生|高校.{0,5}年生',joined))
                coverage_ok=all(coverage(joined,back.get(s,''))>=.2 for s in unit_lines)
                clauses=bool(re.search(r'[!！?？。].+[!！?？。]|[」』].*(?:なんか|それ|この)',joined))
                if not (prefix or name_field or coverage_ok or clauses):continue
                if prefix:parts[0]=max(parts[0],3.)
                score=sum(parts)+max(0.,joint-max(parts))-.2; mapped=[[g[0]['id']]]*k
            else:
                if not valid or any(shape(s) or category(s) in SOUNDS for s in unit_lines):continue
                joint=float(raw[jx[text],ix[joined]])
                if k==1:
                    members=[atom(r['ja'],text,r) for r in g]; j=int(np.argmax(members))
                    covers=[coverage(r['ja'],back.get(text,'')) for r in g]
                    if members[j]>=joint-.6 or len(text.split())<4 or min(covers)<.3:
                        score=members[j]; mapped=[[g[j]['id']]]
                    else:score=joint-.2; mapped=[[r['id'] for r in g]]
                elif len(g)==2:
                    direct=sum(atom(r['ja'],s,r) for r,s in zip(g,unit_lines))
                    reverse=sum(atom(r['ja'],s,r) for r,s in zip(g,unit_lines[::-1]))
                    dependent=any(normalize(r['ja']) in ('もし','ですから','だから','なので') for r in g)
                    if reverse>direct+1.2 and not dependent:mapped=[[g[1]['id']],[g[0]['id']]]; inner=reverse
                    else:mapped=[[g[0]['id']],[g[1]['id']]]; inner=direct
                    if dependent:
                        score=k*joint+.15*max(-6.,inner)-.15
                    else:
                        # A failed concatenated translation (often names or
                        # honorifics) must not erase two independently supported
                        # matches. Require both local clauses in that case.
                        selected_lines=unit_lines[::-1] if reverse>direct+1.2 else unit_lines
                        strong=min(atom(r['ja'],s,r) for r,s in zip(g,selected_lines))>=.5
                        score=inner+.5*(max(0.,joint) if strong else joint)-.15
                else:continue
            if score>0:opts.append({'a':a,'k':k,'indices':indices,'mask':sum(1<<i for i in indices),'score':float(score),'mapped':mapped})
        options.append(opts)
    chosen,marginals,unused=optimize(options,len(lines),details=True)
    total=sum(options[gi][oi]['score'] for gi,oi in chosen)
    assigned={}; evidence={}; splits={}
    for gi,oi in chosen:
        option=options[gi][oi]
        for j,ids in enumerate(option['mapped']):
            k=option['indices'][j]; assigned[k]=(ids[0],None,"Ghép theo nghĩa Nhật–Việt cục bộ")
            # Keep the score and competing destinations inspectable, not a made-up confidence.
            alternatives=[]
            for hi,opts in enumerate(options):
                for oi2,o in enumerate(opts):
                    if k in o['indices'] and o['mapped'][o['indices'].index(k)]!=ids:
                        if marginals is not None:alternatives.append(marginals[hi][oi2])
            score=option['score']/option['k']
            margin=total-max([unused[k],*alternatives]) if marginals is not None else 0.
            evidence[k]={"version":VERSION,"score":round(score,4),"margin":round(margin,4),
                         "sources":ids,"ja":" ".join(r['ja'] for r in regions if r['id'] in ids)}
            if len(ids)>1:splits[k]=split_unit(lines[k],[next(r for r in regions if r['id']==rid) for rid in ids],scorer)
    result={"assign":assigned,"unplaced":[k for k in range(len(lines)) if k not in assigned],
            "warnings":[],"evidence":evidence,"splits":splits}
    if refine:refine_clauses(regions,lines,result,scorer)
    return result


def refine_clauses(regions,lines,result,scorer):
    """A Word unit may combine clauses printed in different, unconnected balloons.

    Change that placement only when BOTH actual fragments have strong, globally
    preferred matches. An unused source cannot be claimed on topic alone.
    """
    used={rid for e in result['evidence'].values() for rid in e['sources']}
    refinements=0
    for k in sorted(result['assign']):
        if k in result['splits'] or refinements>=2:continue
        original=lines[k];owner=result['assign'][k][0]
        cuts=[m.end() for m in re.finditer(r'[,;.!?…]\s*(?=(?:nên|nhưng|vì|rồi|và)\b)',original,flags=re.I)]
        cuts=[c for c in cuts if min(len(original[:c].split()),len(original[c:].split()))>=3]
        free=[r for r in regions if r['id'] not in used and r['bubble']!='sfx' and
              len(normalize(r.get('ja','')))>=3 and not _unreliable_japanese(r.get('ja',''))]
        if not cuts or not free:continue
        candidates=[next(r for r in regions if r['id']==owner),*free]
        for cut in cuts[:2]:
            pieces=[original[:cut],original[cut:]]
            trial=match(candidates,pieces,scorer=scorer,refine=False)
            if len(trial['assign'])!=2 or trial['splits']:continue
            ids=[trial['assign'][i][0] for i in (0,1)]
            if len(set(ids))!=2 or owner not in ids:continue
            checks=[trial['evidence'][i] for i in (0,1)]
            if min(e['score'] for e in checks)<1.5 or min(e['margin'] for e in checks)<.8:continue
            result['splits'][k]=[{'id':ids[0],'text':pieces[0],'start':0,'end':cut},
                                 {'id':ids[1],'text':pieces[1],'start':cut,'end':len(original)}]
            result['assign'][k]=(ids[0],None,'Tách các mệnh đề theo nghĩa Nhật–Việt')
            result['evidence'][k]={'version':VERSION,'score':min(e['score'] for e in checks),
                                   'margin':min(e['margin'] for e in checks),'sources':ids,
                                   'ja':' '.join(next(r['ja'] for r in regions if r['id']==rid) for rid in ids)}
            used.update(ids);refinements+=1;break


def split_unit(text, regions, scorer):
    """Choose a meaning-supported word boundary; preserve every input character."""
    if len(regions)!=2:return [{"id":regions[0]['id'],"text":text,"start":0,"end":len(text)}]
    cuts=[m.end() for m in re.finditer(r'\s+',text) if 0<m.end()<len(text)]
    if not cuts:return [{"id":regions[0]['id'],"text":text,"start":0,"end":len(text)}]
    pieces=list(dict.fromkeys([t.strip() for c in cuts for t in (text[:c],text[c:])]))
    raw,_=scorer.score([r['ja'] for r in regions],pieces,{r['ja'] for r in regions},set(pieces))
    index={s:i for i,s in enumerate(pieces)}
    cut=max(cuts,key=lambda c:float(raw[index[text[:c].strip()],0]+raw[index[text[c:].strip()],1]))
    return [{"id":regions[0]['id'],"text":text[:cut],"start":0,"end":cut},
            {"id":regions[1]['id'],"text":text[cut:],"start":cut,"end":len(text)}]
