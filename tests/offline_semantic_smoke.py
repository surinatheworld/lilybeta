"""Run manually after installing the real local weights; no Photoshop or service required."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import socket
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.core.semantic_match import match
from app.core.workflow import new_region


def forbidden(*args,**kwargs):raise AssertionError('Runtime network forbidden')


socket.create_connection=forbidden
socket.socket.connect=forbidden
sources=[('A','今日は図書館に行きます'),('B','明日は家で勉強します')]
regions=[]
for i,(rid,ja) in enumerate(sources):
    r=new_region(rid,[i*100,0,i*100+80,150]);r.update(ja=ja,ja_src='manual',order=i)
    regions.append(r)
lines=['Ngày mai tôi sẽ học ở nhà.','thịch','Hôm nay tôi đến thư viện.']
result=match(regions,lines)
assert {i:v[0] for i,v in result['assign'].items()}=={0:'B',2:'A'},result
assert result['unplaced']==[1],result
warm=match(regions,lines)
assert warm['assign']==result['assign'] and warm['unplaced']==result['unplaced'],(result,warm)
print('Real offline models: shuffled meaning pairs correct; absent SFX unplaced; no network.')
