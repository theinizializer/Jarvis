"""Scrollable native conversation log with formatted and selectable text."""
import re
import tkinter as tk
from tkinter import font as tkfont


class ChatCanvas(tk.Canvas):
    def __init__(self,parent,**options):
        super().__init__(parent,bg=options.get('bg','#0E1117'),highlightthickness=0,
                         height=280,width=300,takefocus=True,cursor='xterm')
        self.styles={};self.chunks=[];self.fonts={};self._scheduled=None;self._state='normal'
        self.bind('<Configure>',self._resize)
        self.bind('<MouseWheel>',self._wheel);self.bind('<Button-4>',self._wheel);self.bind('<Button-5>',self._wheel)
        self.bind('<Control-c>',lambda _:self.copy())
        self.bind('<Control-a>',lambda _:self._select_all())
        self.bind('<Button-1>',self._select)
        self.bind('<B1-Motion>',self._drag)
        menu=tk.Menu(self,tearoff=False)
        menu.add_command(label='Copia selezione',command=self.copy)
        menu.add_command(label='Copia conversazione',command=lambda:self.copy(all_text=True))
        self.bind('<Button-3>',lambda e:menu.tk_popup(e.x_root,e.y_root))

    def configure(self,cnf=None,**kwargs):
        state_changed='state' in kwargs
        if 'state' in kwargs:self._state=kwargs.pop('state')
        result=super().configure(cnf,**kwargs)
        if state_changed and self._state=='disabled':self._schedule()
        return result
    config=configure

    def get(self,first='1.0',last='end'):
        return ''.join(text for text,_ in self.chunks)

    def insert(self,index,text,tags=()):
        self.chunks.append((str(text),(tags,) if isinstance(tags,str) else tuple(tags)))

    def delete(self,first,last=None):
        if first in ('1.0',0):self.chunks.clear();self._schedule()
        else:super().delete(first)

    def tag_configure(self,name,**options):
        self.styles.setdefault(name,{}).update(options)
    tag_config=tag_configure

    def _schedule(self):
        if self._scheduled is None:self._scheduled=self.after_idle(self._render)

    def _font(self,tags):
        spec=('Noto Sans',13)
        for tag in tags:spec=self.styles.get(tag,{}).get('font',spec)
        key=str(spec)
        if key not in self.fonts:self.fonts[key]=tkfont.Font(self,font=spec)
        return self.fonts[key]

    def _resize(self,event=None):
        self._schedule()

    def _wheel(self,event):
        direction=-1 if getattr(event,'num',None)==4 or getattr(event,'delta',0)>0 else 1
        self.yview_scroll(direction*3,'units')
        return 'break'

    def see(self,index):
        if self._scheduled is not None:
            self.after_cancel(self._scheduled);self._scheduled=None;self._render()
        self.yview_moveto(1)

    def _draw_runs(self,runs,left,right,y,user=False):
        lines=[];line=[];used=0;available=right-left
        for text,tags in runs:
            font=self._font(tags)
            color='#ECF1F7'
            for tag in tags:color=self.styles.get(tag,{}).get('foreground',color)
            for word in re.findall(r'\n|[^\S\n]+|[^\s]+',text):
                if word=='\n':lines.append(line);line=[];used=0;continue
                width=font.measure(word)
                if used+width>available and line:lines.append(line);line=[];used=0
                # Bound long unbroken paths/URLs as well as ordinary words.
                while width>available:
                    size=max(1,int(len(word)*available/max(1,width)))
                    part,word=word[:size],word[size:]
                    lines.append([(part,tags,font,color,font.measure(part))]);width=font.measure(word)
                if word:line.append((word,tags,font,color,width));used+=width
        if line:lines.append(line)
        while lines and not lines[-1]:lines.pop()
        for line in lines:
            ascent=max((p[2].metrics('ascent') for p in line),default=16)
            height=max((p[2].metrics('linespace') for p in line),default=23)+5
            x=right-sum(p[4] for p in line) if user else left
            for word,tags,font,color,width in line:
                background=next((self.styles[t]['background'] for t in reversed(tags) if 'background' in self.styles.get(t,{})),None)
                if background:self.create_rectangle(x,y,x+width,y+height,fill=background,outline='',tags='render')
                self.create_text(x,y+ascent-font.metrics('ascent'),text=word,font=font,fill=color,anchor='nw',tags=('render','copytext'))
                x+=width
            y+=height
        return y

    def _render(self):
        self._scheduled=None
        super().delete('render')
        width=max(240,self.winfo_width());margin=max(20,(width-800)//2)
        blocks=[]
        for text,tags in self.chunks:
            if any(t in tags for t in ('assistant_name','user_name','welcome','welcome_hint')):
                blocks.append({'kind':tags[0],'label':text.strip(),'runs':[]})
            elif blocks:blocks[-1]['runs'].append((text,tags))
        y=30
        for block in blocks:
            kind=block['kind'];user=kind=='user_name'
            if kind.startswith('welcome'):
                y=max(y,32)
                item=self.create_text(width/2,y,text=block['label'],font=self._font((kind,)),fill=self.styles.get(kind,{}).get('foreground','#ECF1F7'),anchor='n',justify='center',width=width-50,tags=('render','copytext'))
                y=self.bbox(item)[3]+18;continue
            y+=12
            self.create_text(width-margin if user else margin,y,text=block['label'],font=self._font((kind,)),fill='#91A1B4' if user else '#55CFE0',anchor='ne' if user else 'nw',tags=('render','copytext'))
            y+=25
            # Strip only the separator inserted after the message, preserving internal newlines.
            runs=block['runs'][:]
            while runs and not runs[-1][0].strip():runs.pop()
            if runs:runs[-1]=(runs[-1][0].rstrip(),runs[-1][1])
            y=self._draw_runs(runs,margin+(60 if user else 0),width-margin,y,user)+22
        self.configure(scrollregion=(0,0,width,max(y+15,self.winfo_height())))

    def _select_all(self):
        self._all_selected=True;return 'break'

    def _select(self,event):
        self.focus_set();self._all_selected=False
        self.select_clear();self._selection_item=None
        items=self.find_overlapping(self.canvasx(event.x),self.canvasy(event.y),self.canvasx(event.x),self.canvasy(event.y))
        for item in reversed(items):
            if 'copytext' in self.gettags(item):
                self._selection_item=item;self.select_from(item,'@%s,%s'%(self.canvasx(event.x),self.canvasy(event.y)));break

    def _drag(self,event):
        item=getattr(self,'_selection_item',None)
        if item:self.select_to(item,'@%s,%s'%(self.canvasx(event.x),self.canvasy(event.y)))

    def copy(self,all_text=False):
        item=self.select_item()
        if item and not all_text and not getattr(self,'_all_selected',False):
            text=self.itemcget(item,'text')[self.index(item,'sel.first'):self.index(item,'sel.last')+1]
        else:text=self.get()
        self.clipboard_clear();self.clipboard_append(text)
        return 'break'
