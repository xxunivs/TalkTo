# 시험지 md → 워드. 가이드·예시는 질문 칸(음영 박스) 안에 넣는다.
import re, sys
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

FONT="맑은 고딕"
GRAY=RGBColor(0x6E,0x6E,0x6E); DARK=RGBColor(0x1a,0x1a,0x1a); BLUE=RGBColor(0x1F,0x4E,0x79)

def shade(p,c):
    e=OxmlElement('w:shd'); e.set(qn('w:val'),'clear'); e.set(qn('w:fill'),c)
    p._p.get_or_add_pPr().append(e)
def rule(p,color='1F4E79',sz='12'):
    b=OxmlElement('w:pBdr'); p._p.get_or_add_pPr().append(b)
    e=OxmlElement('w:bottom'); e.set(qn('w:val'),'single'); e.set(qn('w:sz'),sz)
    e.set(qn('w:space'),'4'); e.set(qn('w:color'),color); b.append(e)

INLINE=re.compile(r'(\*\*.+?\*\*|`[^`]+`)')
def rich(p,text,size=10,base=None,bold=False):
    for tok in INLINE.split(text):
        if not tok: continue
        if tok.startswith('**') and tok.endswith('**'):
            r=p.add_run(tok[2:-2]); r.bold=True
        elif tok.startswith('`') and tok.endswith('`'):
            r=p.add_run(tok[1:-1]); r.font.size=Pt(size-1.5); r.font.color.rgb=GRAY; continue
        else:
            r=p.add_run(tok); r.bold=bold
        r.font.size=Pt(size); r.font.color.rgb=base or DARK

def para(doc,text="",size=10,bold=False,color=None,before=0,after=4,indent=0):
    p=doc.add_paragraph(); f=p.paragraph_format
    f.space_before=Pt(before); f.space_after=Pt(after); f.left_indent=Cm(indent); f.line_spacing=1.3
    if text: rich(p,text,size,color,bold)
    return p

def table(doc,rows):
    t=doc.add_table(rows=0,cols=len(rows[0])); t.style="Table Grid"
    for i,row in enumerate(rows):
        cells=t.add_row().cells
        for j,v in enumerate(row):
            p=cells[j].paragraphs[0]; p.paragraph_format.space_after=Pt(3)
            if i==0:
                r=p.add_run(v); r.bold=True; r.font.size=Pt(8.5); shade(p,'EFEFEF')
            else: rich(p,v,9)
    doc.add_paragraph().paragraph_format.space_after=Pt(2)

QID=re.compile(r'^\*\*([A-Za-z0-9\-]+) · (.*?)\*\*(?:\s+`(.+?)`)?\s*$')

doc=Document()
for s in doc.sections:
    s.left_margin=s.right_margin=Cm(2.2); s.top_margin=s.bottom_margin=Cm(1.8)
st=doc.styles["Normal"]; st.font.name=FONT; st.font.size=Pt(10)
st._element.rPr.rFonts.set(qn('w:eastAsia'),FONT)
para(doc,"Persona Intake 문항",size=20,bold=True,after=2)
para(doc,"설계 원칙·형식 규격은 Persona_Intake_설계규격.md",size=9,color=GRAY,after=14)

lines=open(sys.argv[1],encoding="utf-8").read().splitlines()
_s=next((i for i,l in enumerate(lines) if l.startswith('# PART')), 0)
lines=[l for l in lines[_s:] if set(l.strip())!={'-'}]

# 문항 단위로 끊어, 그 안의 > 안내줄을 질문 칸으로 끌어올린다
blocks=[]; cur=None
for ln in lines:
    if QID.match(ln) or ln.startswith('#') or (ln.startswith('##')):
        cur={'head':ln,'body':[]}; blocks.append(cur)
    elif cur is None:
        blocks.append({'head':None,'body':[ln]}); cur=blocks[-1]
    else: cur['body'].append(ln)

for b in blocks:
    h=b['head']
    guides=[l[2:].strip() for l in b['body'] if l.startswith('> ')]
    body=[l for l in b['body'] if not l.startswith('> ')]
    if h and h.startswith('#'):
        lvl=len(h)-len(h.lstrip('#')); txt=h.lstrip('#').strip()
        if lvl==1: p=para(doc,txt,size=15,bold=True,color=BLUE,before=22,after=8); rule(p)
        else: para(doc,txt,size=12,bold=True,color=BLUE,before=16,after=6)
    elif h:
        qid,txt,fmt=QID.match(h).groups()
        p=doc.add_paragraph(); f=p.paragraph_format
        f.space_before=Pt(13); f.space_after=Pt(6); f.left_indent=Cm(0.2); f.right_indent=Cm(0.2)
        f.line_spacing=1.25; shade(p,'F1F5F9')
        r=p.add_run(qid+"  "); r.bold=True; r.font.size=Pt(10.5)
        rich(p,txt,10.5,bold=True)
        if fmt:
            r=p.add_run("   "+fmt); r.font.size=Pt(8.5); r.font.color.rgb=GRAY
        for g in guides:
            p.add_run().add_break()
            rich(p,g,8.5,base=GRAY)
        guides=[]
    buf=[]
    def flush():
        global buf
        if buf:
            rows=[[c.strip() for c in r.strip().strip('|').split('|')] for r in buf
                  if not re.match(r'^\|[\s:\-\|]+\|$',r.strip())]
            if rows: table(doc,rows)
            buf=[]
    for s in body:
        s=s.rstrip()
        if s.startswith('|'): buf.append(s); continue
        flush()
        if not s.strip(): continue
        if s.startswith('- '): para(doc,s[2:],size=10,indent=0.6,after=3); continue
        if s.startswith('*') and s.endswith('*') and not s.startswith('**'):
            para(doc,s.strip('*'),size=9,color=GRAY,after=6); continue
        if s.startswith('**') and s.endswith('**'):
            para(doc,s.strip('*'),size=10.5,bold=True,before=10,after=3); continue
        para(doc,s,size=10,indent=0.4,after=3)
    flush()
    for g in guides: para(doc,g,size=8.5,color=GRAY,indent=0.6,after=2)

doc.save(sys.argv[2]); print("saved:",sys.argv[2])
