import re,unicodedata
from pathlib import Path
import numpy as np
from .semantic_review import _unreliable_japanese
from .ocr import normalize,coverage

def plain(s):
    s=s.lower().replace('đ','d')
    s=''.join(c for c in unicodedata.normalize('NFD',s) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+',s))

SOUNDS={
 'thich':['どき','ドキ','どく','ドク'], 'siet':['すみっ','する','ぎゅ','ギュ','ぐっ','グッ','にぎ','ニギ','すり','スリ'],
 'vut':['ばっ','バッ','すっ','スッ','さっ','サッ','しゅ','シュ'],
 'pi':['ぽち','ポチ','ぴ','ピ'], 'pikon':['ぴこん','ピコン','ぴこ','ピコ'],
 'trung':['じっ','ジッ','じー','ジー'], 'cham':['ちょん','チョン','とん','トン','すっ','スッ'],
 'giat thot':['びく','ビク','ぼっ','ボッ'],
 'phu':['ふー','フー','ふう','ほっ','ホッ'],
 'hay':['はー','ハー','は〜','ハ〜','はあ','ハア'],
 'hi':['ふふ','フフ','にっ','ニッ','にこ','ニコ','ほっ','ホッ'],
 'hi hi':['ふふ','フフ','にっ','ニッ','にこ','ニコ'],
 'he he':['ふふ','フフ','にっ','ニッ'],
 'woa':['わあ','ワア','うお','ウオ','うわ','ウワ','わー','ワー'],
 'ao':['ざ','ザ','ばしゃ','バシャ','わあ','ワア'],
 'lung lo':['すい','スイ','すー','スー'],
 'ba':['のっ','ノッ','の!','の！','の'], 'mot hai':['せーの','セーノ','せー','セー','せえ','セエ','いち','一二'],
}
ACKS={
 'vang':['はい','ハイ','ええ','エエ'], 'u':['おうよ','オウヨ','うん','ウン','ああ','アア','おう','オウ','あー','アー'],
  'um':['あと','アト','えっと','エット','あの','アノ','うん','ウン','ああ','アア'],
 'a':['あ','ア','お','オ'], 'o':['お','オ','おお','オオ','うい','ウイ'],
 'bye':['じゃーな','ジャーナ','またな','マタナ','ばい','バイ'],
 'ha':['え','エ','は','ハ','んな','ンナ'], 'e':['あれ','アレ','え','エ','って','ッテ','あれ','アレ'],
 'cai':['ちょ','チョ','なに','ナニ','何'],
 'get go':['えい','エイ','よし','ヨシ'],
 'nhanh the':['早','はや','ハヤ'],
 'that a':['マジ','まじ','本当','ほんとう'],
 'me oi':['ママ','まま','お母さん','おかあさん'],
}

def shape(s):
    s=unicodedata.normalize('NFKC',s)
    if any(unicodedata.category(c)[0] in 'LN' for c in s):return ''
    return ''.join(c for c in '?!.' if c in s or (c=='.' and any(x in s for x in '…・‥')))

def category(s):
    p=plain(s)
    if re.fullmatch(r'w[oau]+',p):return 'woa'
    if p.startswith('a ') and p[2:] in ACKS:return p[2:]
    return p


def japanese_phrase(text):
    """Lettering styled as an effect can still contain spoken words."""
    compact = normalize(text)
    return len(compact) >= 7 and bool(re.search(r"[一-龯々][ぁ-ゖ]*(?:の|に|が|を|で|は|へ|て|と)[一-龯々ァ-ヺぁ-ゖ]", compact))


def kana_name(text):
    """Hepburn reading for short kana/furigana name anchors, without a dictionary."""
    text=''.join(chr(ord(c)-0x60) if 'ァ'<=c<='ヶ' else c for c in text)
    rows=[('あいうえお','a i u e o'),('かきくけこ','ka ki ku ke ko'),('さしすせそ','sa shi su se so'),
          ('たちつてと','ta chi tsu te to'),('なにぬねの','na ni nu ne no'),('はひふへほ','ha hi fu he ho'),
          ('まみむめも','ma mi mu me mo'),('やゆよ','ya yu yo'),('らりるれろ','ra ri ru re ro'),
          ('わをん','wa o n'),('がぎぐげご','ga gi gu ge go'),('ざじずぜぞ','za ji zu ze zo'),
          ('だぢづでど','da ji zu de do'),('ばびぶべぼ','ba bi bu be bo'),('ぱぴぷぺぽ','pa pi pu pe po')]
    table={c:r for chars,reading in rows for c,r in zip(chars,reading.split())}
    out=[];double=False
    for c in text:
        if c=='っ':double=True;continue
        if c in 'ゃゅょ' and out:
            previous=out.pop();vowel={'ゃ':'a','ゅ':'u','ょ':'o'}[c]
            out.append({'shi':'sh','chi':'ch','ji':'j'}.get(previous,previous[:-1]+'y')+vowel);continue
        if c=='ー' and out:out.append(out[-1][-1]);continue
        if c not in table:return ''
        syllable=table[c]
        out.append((syllable[0] if double else '')+syllable);double=False
    return ''.join(out)


def name_key(text):
    return re.sub(r'([aeiou])\1+',r'\1',plain(text).replace(' ','').replace('l','r').replace('ou','o'))

def lexical(line,ja):
    a,b=shape(line),shape(ja)
    if a or b:return 6. if a and a==b else -15.
    key=category(line);word=normalize(ja)
    if _unreliable_japanese(ja):return -15.
    if re.fullmatch(r'\W*[A-Z][A-Za-zāīūēō]+(?:[ -][A-Z][A-Za-zāīūēō]+)?\W*',line) and len(plain(line))>=3:
        if any(name_key(kana_name(k))==name_key(line) for k in re.findall(r'[ぁ-ゖァ-ヺー]{2,}',ja)):
            return 6.
    if key in SOUNDS:
        # Long-vowel removal turns すー into す. A one-kana sound must
        # not match the す in an unrelated sentence such as ですから.
        hit=any((normalize(x) in word if len(normalize(x))>=2 else normalize(x)==word) for x in SOUNDS[key])
        return 6. if hit and len(word)<12 else -15.
    if key in ACKS and any(normalize(x)==word or word.startswith(normalize(x)) for x in ACKS[key]):
        return 6. if any(normalize(x)==word for x in ACKS[key]) else 1.4
    return 0.
