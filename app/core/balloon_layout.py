"""Keep rendered text inside the specific lobe selected by semantic assignment."""
from collections import Counter

import cv2
import numpy as np

from .typeset import place_in_shape
from .typeset_profiles import profile_style


def panel_limits(page, anchor):
    """Straight panel borders stop a leaked white balloon mask at the gutter."""
    image=getattr(page,'original',None)
    if image is None:return (0,0,*page.size)
    lines=getattr(page,'_lilyzono_panel_lines',None)
    if lines is None:
        h,w=image.shape[:2];black=(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)<80).astype(np.uint8)
        lines=[]
        for vertical,kernel in ((True,(1,max(40,round(h*.1)))),(False,(max(40,round(w*.1)),1))):
            mask=cv2.morphologyEx(black,cv2.MORPH_OPEN,np.ones(kernel[::-1],np.uint8))
            count,labels,stats,centers=cv2.connectedComponentsWithStats(mask)
            for x,y,bw,bh,area in stats[1:]:
                if (bw if vertical else bh)>max(12,round(min(w,h)*.02)):continue
                lines.append((vertical,int(x),int(y),int(x+bw),int(y+bh)))
        page._lilyzono_panel_lines=lines
    cx,cy=anchor;left=top=0;right,bottom=page.size
    for vertical,x,y,X,Y in lines:
        if vertical and y<=cy<=Y:
            if X<cx-6:left=max(left,X+2)
            elif x>cx+6:right=min(right,x-2)
        elif not vertical and x<=cx<=X:
            if Y<cy-6:top=max(top,Y+2)
            elif y>cy+6:bottom=min(bottom,y-2)
    return left,top,right,bottom


def constrain_lobes(page, owners):
    """Apply detected per-lobe geometry before generic balloon fitting can widen it.

    These rectangles live only on rendering Regions; they do not change manual
    document fields or the original Japanese/source bounding boxes.
    """
    def owner(region):
        return owners.get(region.uid) or owners.get(region.group)

    counts=Counter(row['id'] for r in page.regions if (row:=owner(r)))
    for region in page.regions:
        row=owner(region)
        if not row or counts[row['id']]!=1 or row.get('src')=='manual' or region.manual_rect:continue
        geometry=row.get('balloon') or {}
        points=np.asarray(geometry.get('outline') or [],np.int32)
        if (region.kind=='sfx' or not geometry.get('split') or geometry.get('uncertain') or
                len(points)<4 or row['bubble'] not in ('round','square','cloud','ray')):continue
        x,y,X,Y=region.box;anchor=((x+X)/2,(y+Y)/2)
        if cv2.pointPolygonTest(points.astype(np.float32),anchor,False)<0:continue
        lo=points.min(0);hi=points.max(0)+1
        left,top,right,bottom=panel_limits(page,anchor)
        lo=np.maximum(lo,(left,top));hi=np.minimum(hi,(right,bottom))
        width,height=(hi-lo).tolist()
        if min(width,height)<16:continue
        mask=np.zeros((height,width),np.uint8)
        cv2.fillPoly(mask,[points-lo],1)
        pad=max(3,round(min(width,height)*.035))
        mask=cv2.erode(mask,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(pad*2+1,pad*2+1)),
                       borderType=cv2.BORDER_CONSTANT,borderValue=0)
        distance=cv2.distanceTransform(np.pad(mask,1),cv2.DIST_L2,5)[1:-1,1:-1]
        _,radius,_,peak=cv2.minMaxLoc(distance)
        if radius<6:continue
        cx,cy=peak[0]+int(lo[0]),peak[1]+int(lo[1])
        rows=[]
        for yy,line in enumerate(mask):
            xs=np.flatnonzero(line)
            if len(xs)>=6:rows.append((yy+int(lo[1]),int(xs[0]+lo[0]),int(xs[-1]+lo[0])))
        if len(rows)<8:continue
        shape=(float(cx),float(cy),tuple(rows))
        region.style=profile_style(page,region,region.style)
        region.auto_cap=page.auto_cap
        rect=place_in_shape(region.text,region.effective_style,shape,anchor)
        if rect is None:
            # A small rectangle inside the largest inscribed circle is safe even
            # when a font/long token prevents the shaped layout algorithm.
            rx,ry=int(radius*.65),int(radius*.7)
            rect=(cx-rx,cy-ry,cx+rx,cy+ry)
        region.manual_rect=tuple(map(int,rect))
        region.text_shape=shape


def constrain_panel_rects(page, owners):
    """Giữ chữ trong panel nếu vùng chữ gốc nằm hẳn bên trong đường viền."""
    for region in page.regions:
        row = owners.get(region.uid) or owners.get(region.group)
        if not row or row.get('src') == 'manual' or region.kind == 'sfx' or region.manual_rect:
            continue
        x, y, right, bottom = region.box
        anchor = ((x + right) / 2, (y + bottom) / 2)
        left, top, panel_right, panel_bottom = panel_limits(page, anchor)
        if not (left <= x < right <= panel_right and top <= y < bottom <= panel_bottom):
            continue
        a, b, c, d = region.text_rect
        # Giữ khoảng thở sát viền khi khung tự động lấn ra ngoài panel.
        rect = (max(a, left + 3) if a < left else a, max(b, top + 3) if b < top else b,
                min(c, panel_right - 3) if c > panel_right else c, min(d, panel_bottom - 3) if d > panel_bottom else d)
        if rect != (a, b, c, d) and min(rect[2] - rect[0], rect[3] - rect[1]) >= 16:
            region.manual_rect = rect
            region.text_shape = None
