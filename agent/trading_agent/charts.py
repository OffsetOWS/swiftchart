"""Render API-supplied candles and labels. Only pixel coordinates are calculated."""
import hashlib
from io import BytesIO
import json
from time import monotonic
from PIL import Image, ImageDraw, ImageFont


class CandleCharts:
    def __init__(self):
        self.cache = {}

    def render(self, data):
        if not data.get('available') or not data.get('candles'):
            raise ValueError('Chart candle data is unavailable')
        key = hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()
        hit = self.cache.get(key)
        if hit and monotonic()-hit[0]<20:
            return hit[1]
        palette = data['palette']
        image = Image.new('RGB',(1280,780),palette['background'])
        draw = ImageDraw.Draw(image)
        font = lambda size: ImageFont.load_default(size=size)
        left,right,top,bottom = 60,1030,115,655
        low,high = data['axis_min'],data['axis_max']
        if high <= low:
            raise ValueError('Invalid chart scale')
        y = lambda value: int(bottom-(float(value)-low)/(high-low)*(bottom-top))
        candles = data['candles']
        step = (right-left)/len(candles)
        x = lambda i: int(left+(i+.5)*step)
        draw.text((left,24),'SWIFTCHART',font=font(19),fill=palette['muted'])
        draw.text((left,52),f"{data['symbol']}  ·  {data['timeframe']}",font=font(31),fill=palette['text'])
        draw.text((left,92),'API candle data · support / resistance · active signal levels',font=font(14),fill=palette['muted'])
        for tick in data['axis_ticks']:
            row=y(tick['value'])
            draw.line((left,row,right,row),fill=palette['grid'],width=1)
            draw.text((right+12,row-9),str(tick['text']),font=font(16),fill=palette['muted'])
        for level in data['levels']:
            a,b=y(level['high']),y(level['low'])
            if a!=b:
                overlay=Image.new('RGBA',image.size,(0,0,0,0));layer=ImageDraw.Draw(overlay)
                colour=level['colour'].lstrip('#');rgb=tuple(int(colour[i:i+2],16) for i in (0,2,4))
                layer.rectangle((left,a,right,b),fill=(*rgb,25))
                image=Image.alpha_composite(image.convert('RGBA'),overlay).convert('RGB');draw=ImageDraw.Draw(image)
            for row in {a,b}:
                for start in range(left,right,14):draw.line((start,row,min(start+8,right),row),fill=level['colour'],width=1)
        for i,candle in enumerate(candles):
            colour=palette['up'] if candle['close']>=candle['open'] else palette['down']
            mid=x(i);width=max(2,int(step*.65));a,b=sorted((y(candle['open']),y(candle['close'])))
            draw.line((mid,y(candle['high']),mid,y(candle['low'])),fill=colour,width=1)
            draw.rectangle((mid-width//2,a,mid+width//2,max(a+2,b)),fill=colour)
        # Keep labels readable when entry/stop/targets lie close together.
        previous=-100
        for level in sorted(data['levels'],key=lambda item:y(item['high'])):
            row=max(y(level['high']),previous+22);row=min(row,bottom+24);previous=row
            # Pillow's bundled font lacks an en dash. Keep API numbers verbatim
            # while using an unambiguous separator supported by the image font.
            label=level['text'].replace('–', ' - ')
            box=draw.textbbox((0,0),label,font=font(14))
            width=box[2]+16
            draw.rectangle((right-width-7,row-11,right-4,row+10),fill=palette['panel'])
            draw.text((right-width+1,row-9),label,font=font(14),fill=level['colour'])
        for tick in data['time_ticks']:
            mid=x(tick['index']);draw.text((max(left,min(mid-45,right-95)),bottom+20),tick['text'],font=font(13),fill=palette['muted'])
        draw.text((left,710),'Data: '+data['as_of']+' · '+str(data['candle_count'])+' candles',font=font(15),fill=palette['muted'])
        draw.text((left,742),'Not financial advice.',font=font(14),fill=palette['muted'])
        result=BytesIO();image.save(result,format='PNG',optimize=True);raw=result.getvalue()
        if len(self.cache)>=32:self.cache.pop(next(iter(self.cache)))
        self.cache[key]=(monotonic(),raw)
        return raw
