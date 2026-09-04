# 시험지(문항·선지·가이드)에 작성본의 답을 얹어 정답지 md 를 만든다.
import re, sys, pathlib

SRC=r'(?:본인|직접|전언|추정)'
def strip_tags(s):
    s = re.sub(r'`\[([^\]]*)\]`', r'[\1]', s)
    s = re.sub(r'\[상태: *([^\]]*)\]', r'[\1]', s)
    def keep(m):
        v = m.group(1).strip()
        if re.match(r'^(모름|확인 필요)', v): return '['+v+']'
        if re.fullmatch(rf'{SRC}(?: *\+ *{SRC})*', v): return ''
        if re.match(r'^(답함|건너뜀|미수집)', v): return ''
        return m.group(0)
    s = re.sub(r'\[([^\]]*)\]', keep, s).replace('`','')
    return re.sub(r'\s*[—–]\s*$','',re.sub(r'  +',' ',s)).rstrip()

def norm(s):
    s = re.split(r'→|:', s)[0]
    s = s.replace('**','').replace('`','').strip()
    s = re.sub(r'^[■□]\s*', '', s)
    s = re.sub(r'^[①-⑩]\s*', '', s)
    s = re.sub(r'\(.*?\)|\s|·|,|\.', '', s)
    return s

# ── 시험지 파싱 ────────────────────────────────
QID = re.compile(r'^\*\*([A-Za-z0-9\-]+) · (.*?)\*\*')
def parse_sheet(p):
    blocks, cur = [], {'id': None, 'lines': []}
    src = pathlib.Path(p).read_text(encoding='utf-8').splitlines()
    src = src[next((i for i,l in enumerate(src) if l.startswith('# PART')), 0):]
    for l in src:
        m = QID.match(l)
        if m or l.startswith('#'):
            blocks.append(cur); cur = {'id': m.group(1) if m else None, 'lines': [l]}
        else:
            cur['lines'].append(l)
    blocks.append(cur)
    return [b for b in blocks if b['lines']]

# ── 작성본 파싱 ────────────────────────────────
def parse_answers(p):
    txt = pathlib.Path(p).read_text(encoding='utf-8')
    ans, cards, skips = {}, [], set()
    cur = None
    for l in txt.splitlines():
        m = re.match(r'^\*\*([A-Za-z0-9\-]+)\*\*\s*(.*)$', l)
        if m:
            cur = m.group(1); rest = m.group(2)
            ans.setdefault(cur, [])
            if re.search(r'건너뜀|해당 없음|해당없음', rest): skips.add(cur)
            # 대괄호·백틱 밖의 콜론에서만 자른다 ([상태: 미수집] 안의 콜론을 피함)
            depth=0; cut=None
            for k,c in enumerate(rest):
                if c in '[`': depth+=1
                elif c in ']`': depth=max(0,depth-1)
                elif c==':' and depth==0: cut=k; break
            if cut is not None: ans[cur].append(rest[cut+1:].strip())
            continue
        m = re.match(r'^\*\*카드 \d+ — (.+?)\*\*\s*(.*)$', l)
        if m:
            cards.append({'name': m.group(1), 'f': {}}); cur = None; continue
        m = re.match(r'^-\s*(M\d-[0-9a-bOE·]+)[^:]*:\s*(.*)$', l)
        if m and cards:
            for part in re.split(r'\s+/\s+(?=M\d-)', m.group(1) + ': ' + m.group(2)):
                mm = re.match(r'^(M\d-[0-9a-bOE·]+)[^:]*:\s*(.*)$', part.strip())
                if mm: cards[-1]['f'][mm.group(1)] = mm.group(2).strip()
            continue
        m = re.match(r'^\|\s*(M\d-\d+|T\d-\d+)\s*\|(.+)$', l)
        if m:
            ans.setdefault(m.group(1), []).append('|' + m.group(2)); continue
        if cur:
            ans[cur].append(l)
            if re.search(r'건너뜀|해당 없음|해당없음', l): skips.add(cur)
    return ans, cards, skips

def m8_split(lines):
    for l in lines:
        if not l.strip().startswith('|'): continue
        cols=[c.strip() for c in l.strip().strip('|').split('|')]
        sel=[c for c in cols if c.startswith('■')]
        i=next((k for k,c in enumerate(cols) if c.startswith('■')), 0)
        say=[c for k,c in enumerate(cols) if k>i and c.startswith('"')]
        return sel, (say[0] if say else None)
    return [], None

def sel_texts(lines, vals=None):
    out = []
    for l in lines:
        for seg in re.split(r'(?=[■□])', l):
            if not seg.startswith('■'): continue
            head = re.split(r'\s+[—–]\s+|\s+·\s+|\s+\[', seg)[0]
            k = norm(head)
            if k:
                out.append(k)
                m = re.search(r'→\s*(.+)$', head)          # "직접 입력 → 덕후 기질"
                if m and vals is not None: vals[k] = m.group(1).strip()
    return [x for x in out if x]

# 답 뒤에 붙은 편집 주석(다른 문항 가리키기)은 잘라낸다
XTAIL=re.compile(r'\s*[—–]\s*[^—–]*?(?:[CMPT]\d[\d\-a-b·]*\s*(?:카드|표|참조)|카드 ?\d|'
                 r'[CMPT]\d[\d\-a-b·]*\s*(?:에서|으로|에 반영|기본값)|확인 필요)[^—–]*$')
def drop_xref(v):
    return XTAIL.sub('', v).strip() if v else v

CARDF=re.compile(r'(시기|감정|확실성|사용 범위|출처)\s*:\s*\**([^·\n(*]+)\**')
SENS=re.compile(r'\s*·?\s*민감도\s*[:]?\s*(낮음|중간|중|높음)')
def pull_card(v):
    """답 문장 뒤에 붙은 카드 필드(시기·감정·사용 범위 등)를 떼어낸다."""
    d={}
    if not v: return v, d
    for k,x in CARDF.findall(v): d.setdefault(k, x.strip(' .·'))
    v=CARDF.sub('', v)
    v=SENS.sub('', v)
    v=re.sub(r'\*{2,}','',v)
    v=re.sub(r'\((?:M\d{3}[^)]*)\)','',v)                 # 인터뷰 정리본 번호
    v=re.sub(r'(?:\s*·\s*){2,}',' ',v)
    v=re.sub(r'\s*[·,]\s*$','',re.sub(r'\s{2,}',' ',v)).strip(' ·')
    return v, d

NOTE=re.compile(r'^(메모|주 작성자|경위는)|기준으로 고정|자동 생성|승계|합류|미기재 방침|의도된 공백')
EMPTYV=re.compile(r'^[\(（]?\s*(없음|해당 ?없음|미수집|없습니다)\s*[\)）]?\.?$')
def free_text(lines):
    for l in lines:
        m = re.search(r'→\s*시기:\s*(.+)$', l)
        if m: return m.group(1).strip()
    for l in lines:
        m = re.match(r'^\s*답:\s*(.+)$', l)
        if m: return m.group(1).strip()
    for l in lines:
        s = re.sub(r'^[—–]\s*', '', l.strip())
        if s and not s.startswith(('|', '>', '■', '□', '**')) and '■' not in s and '□' not in s \
           and not NOTE.search(s):
            return s
    return None

def cells(l): return [c.strip() for c in l.strip().strip('|').split('|')]
ALIAS={'언제쓰는말':'상황','언제':'상황','규칙':'하지않아야할말행동',
       '대신어떻게':'대신어떻게하면좋을까요','무엇을하던곳':'무엇을하던곳','어떻게얼마나':'어떻게얼마나',
       '얽힌기억':'얽힌기억','그분과의관계':'관계','받는사람':'받는사람','언제들려줄지':'언제'}
def key(s):
    s=s.split('<br>')[0]                    # 머리행에 붙인 선지는 이름에서 뺀다
    s=re.sub(r'[\s·()]|하시던|하는|인가요|입니까|적어주세요','',s)
    return ALIAS.get(s,s)

def fit_rows(arows, thead):
    """답 표를 시험지 표의 열 순서에 맞춘다."""
    if not arows: return []
    tcols=[key(c) for c in cells(thead)]
    ahead=None
    for r in arows:
        c=cells(r)
        need = 1 if len(tcols)<=2 else max(2, len(tcols)//2)
        if sum(1 for x in c if key(x) in tcols) >= need: ahead=c; break
    if ahead is None:
        if len(tcols)==1:
            # 한 열짜리 표: 답의 머리행을 버리고 내용이 든 열만 남긴다
            rows=[cells(r) for r in arows]
            LAB={'규칙','표현','항목','주제','것','음식','장소','지역','이름','인물','대상','받는 사람','#'}
            rows=[c for c in rows if not (len(c)>1 and sum(1 for x in c if x in LAB) >= 2)]
            if not rows: return []
            width=max(len(c) for c in rows)
            j=max(range(width), key=lambda k: sum(len(c[k]) for c in rows if k<len(c)))
            return ['| ' + (c[j] if j<len(c) else '') + ' |' for c in rows]
        return [r for r in arows]
    idx=[]
    for tc in tcols:
        j=next((k for k,x in enumerate(ahead) if key(x)==tc), None)
        if j is None:
            j=next((k for k,x in enumerate(ahead)
                    if key(x) and (key(x) in tc or tc in key(x))), None)
        idx.append(j)
    out=[]
    for r in arows:
        c=cells(r)
        if c==ahead: continue
        out.append('| ' + ' | '.join((c[j] if (j is not None and j<len(c)) else '') for j in idx) + ' |')
    return out

def rows_of(lines):
    rows = [l for l in lines if l.strip().startswith('|')
            and not re.match(r'^\|[\s:\|]*-[\s:\-\|]*\|$', l.strip())]
    return rows

# ── 병합 ──────────────────────────────────────
COND=re.compile(r'^\*\*[A-Za-z0-9\-]+ · \((?:[PCMT]\d[\w\-]*에서|본인|타인)')

def axis_of(ans):
    def picked(i):
        """그 문항에서 실제로 고른(■) 선지들만."""
        out=[]
        for l in ans.get(i) or []:
            for seg in re.split(r'(?=[■□])', l):
                if seg.startswith('■'): out.append(seg)
        return " ".join(out)
    return {
        'self':     '본인' in picked('P1-02'),
        'deceased': '돌아가셨' in picked('P1-04'),
        'public':   '공개'   in picked('P1-06'),
        'period':   '특정 시기' in picked('P1-05'),
        'tell_hard':'말하겠다'  in picked('M5-07A'),
    }

def merge(sheet, ansfile, out, title):
    blocks = parse_sheet(sheet)
    ans, cards, skips = parse_answers(ansfile)
    ax = axis_of(ans)
    res, used = [], set()

    def render(b, a, card_name=None):
        lines = b['lines'][:]
        if card_name:
            lines = [lines[0]] + lines[1:]
        i = b['id'] or ''
        if   i.startswith('T1-'): skipped = not ax['deceased']
        elif i.startswith('T2-'): skipped = not ax['self']
        elif i.startswith('T3-'): skipped = ax['self'] or ax['deceased']
        elif i.startswith('T4-'): skipped = not (ax['self'] and ax['period'])
        elif i in ('P1-03','P1-08'):   skipped = ax['self']
        elif i == 'M9-02':             skipped = not ax['self']
        elif i == 'P1-07A':            skipped = ax['public']
        elif i in ('P1-07B','P1-07C'): skipped = not ax['public']
        elif i == 'M5-07B':            skipped = not ax['tell_hard']
        elif COND.match(b['lines'][0]):
            skipped = not (a and any(x.strip() for x in a))   # 그 밖의 조건부
        else: skipped = False
        if card_name: skipped = False
        a = [drop_xref(strip_tags(x)) for x in a] if a else a
        if a and b['id'] and b['id'].startswith(('M8-','T1-02b')):
            s8, say = m8_split(a)
            sel = [norm(x) for x in s8]; val = say; arows = []; selval = {}
        else:
            selval = {}
            sel = sel_texts(a, selval) if a else []
            val = free_text(a) if a else None
        arows = arows if (a and b['id'] and b['id'].startswith('M8-')) else (rows_of(a) if a else [])
        arows = [r for r in arows if not re.match(r'^\|\s*(표현|음식|장소|것|규칙|지역|이름|인물|주제|받는|항목)\s*\|', r)]
        if val: val = re.sub(r'^[가-힣A-Za-z0-9 ]{1,14}\s*\([^)]{0,20}\)\s*:\s*', '', val.strip())
        if val and EMPTYV.match(val.strip()): val = None
        # "라벨: 값" 짝은 답 전체에서 모은다 (선지와 한 줄에 섞여 있어도)
        pairs = {}
        for x in (a or []):
            x = re.sub(r'^\s*답\s*:\s*', '', x)          # "답:" 머리말은 라벨이 아니다
            for k, v in re.findall(r'([가-힣]+)\s*:\s*([^·\n|]+?)(?=\s*·|\s*$)', x):
                pairs.setdefault(k.strip(), v.strip())
        val, card = pull_card(val)
        outl, in_tbl, tbl_done, marked, wrote = [], False, False, False, False
        if skipped: outl.append('<!--dim-->')
        first = True
        for l in lines:
            s = l.rstrip()
            if first and outl and outl[0]=='<!--dim-->':
                outl.insert(0, s); first = False; continue
            first = False
            if s.startswith('|'):
                if not in_tbl: in_tbl = True; outl.append(s); continue   # 머리행
                if re.match(r'^\|[\s:\|]*-[\s:\-\|]*\|$', s.strip()): outl.append(s); continue
                if not tbl_done:
                    rows = fit_rows(arows, outl[-2] if len(outl)>=2 else s) if arows else []
                    outl.extend(rows if rows else [s])   # 답이 없으면 빈 칸을 남긴다
                    tbl_done = True
                continue
            in_tbl = False
            if card and re.match(r'^(시기|감정|확실성|사용 범위):', s.strip()):
                def fill(mm):
                    k=mm.group(1); v=card.get(k)
                    return f"{k}: <u>{v}</u>" if v else mm.group(0)
                s = re.sub(r'(시기|감정): _{3,}', fill, s)
                for k in ('확실성','사용 범위'):
                    v = (card.get(k) or '').split('—')[0].split('(')[0].strip()
                    if v and k in s:
                        s = re.sub(r'□ ('+re.escape(v)+r')', r'■ \1', s)
                outl.append(s); continue
            if '□' in s and sel:
                parts = re.split(r'(?=[■□])', s)
                keys = [norm(x) if x.startswith('□') else None for x in parts]
                hit = set()
                for av in sel:
                    exact = [i for i,k in enumerate(keys) if k and k == av]
                    pre   = [i for i,k in enumerate(keys) if k and (k.startswith(av) or av.startswith(k))]
                    if exact: hit.add(exact[0])
                    elif len(pre) == 1: hit.add(pre[0])      # 한 곳에만 걸릴 때만 인정
                new = ['■' + x[1:] if i in hit else x for i,x in enumerate(parts)]
                marked = marked or bool(hit)
                for lab, v in pairs.items():
                    if v.strip()=='모름' and re.search(re.escape(lab)+r'\s*:\s*_{3,}', s):
                        new=[('■'+x[1:] if x.startswith('□') and norm(x)=='모름' else x) for x in new]
                line=''.join(new)
                marked=[x for x in new if x.startswith('■')]
                if '____' in line and pairs and re.search(r'[가-힣]+:\s*_{3,}', line):
                    def put(mm):
                        v=(pairs.get(mm.group(1)) or '').strip()
                        if v == '모름': return mm.group(0)      # 옆의 □ 모름 을 대신 체크
                        return f"{mm.group(1)}: <u>{v}</u>" if v else mm.group(0)
                    line2=re.sub(r'([가-힣]+)\s*:\s*_{3,}', put, line)
                    if line2!=line: outl.append(line2); val=None; continue
                if '____' in line and marked and '____' in marked[-1]:
                    own = selval.get(norm(marked[-1]))
                    fillv = own or (val.strip().strip('"') if val else None)
                    if fillv:
                        line=re.sub(r'_{3,}', '<u>'+fillv+'</u>', line)
                        if not own: val=None
                outl.append(line); continue
            if '____' in s and val and val.strip()=='모름' and '□ 모름' in s:
                outl.append(s.replace('□ 모름','■ 모름').replace('_'*20,'_'*20)); val=None; continue
            if '____' in s and val:
                v = val.strip().strip('"')
                n = len(re.findall(r'_{3,}', s))
                parts = [x.strip() for x in re.split(r'\s*/\s*', v)] if n > 1 else [v]
                if n > 1 and len(parts) == n:
                    it = iter(parts)
                    s = re.sub(r'_{3,}', lambda m: '<u>'+next(it)+'</u>', s)
                else:
                    s = re.sub(r'_{3,}', '<u>'+v+'</u>', s, count=1)
                    s = re.sub(r'_{3,}', '', s)
                outl.append(s); val=None; continue
            if re.match(r'^_{4,}$', s.strip()):
                if val: outl.append('<u>'+val+'</u>'); val = None; wrote = True
                elif not wrote: outl.append(s)
                continue
            outl.append(s)
        if val and not marked and not any(val[:12] in x for x in outl):
            outl.append(val)
        return outl

    dim_head = None
    for b in blocks:
        h = b['lines'][0] if b['lines'] else ''
        if h.startswith('#'):
            m2 = re.match(r'^#{2,3} (T[1-4])\.', h)
            if m2:
                k = m2.group(1)
                dim_head = (k=='T1' and not ax['deceased']) or (k=='T2' and not ax['self']) \
                        or (k=='T3' and (ax['self'] or ax['deceased'])) \
                        or (k=='T4' and not (ax['self'] and ax['period']))
            else: dim_head = False
            if dim_head:
                res.append(h); res.append('<!--dim-->'); res.extend(b['lines'][1:]); continue
        if b['id'] == 'M2-01' and cards:
            for n, c in enumerate(cards, 1):
                res.append(f"**카드 {n} · {c['name']}**")
                for bb in blocks:
                    if bb['id'] and bb['id'].startswith('M2-') and bb['id']!='M2-11':
                        v = c['f'].get(bb['id'])
                        res.extend(render(bb, [v] if v else None))
                        res.append('')
            continue
        if b['id'] and b['id'].startswith('M2-') and b['id']!='M2-11' and cards: continue
        a = ans.get(b['id'])
        if a: used.add(b['id'])
        res.extend(render(b, a))
    txt = title + "\n\n<!--answersheet-->\n\n" + "\n".join(res) + "\n"
    # 체크박스가 같이 있는 표 칸은 값에 밑줄 (단독 문항과 같은 모양)
    txt = re.sub(r'(?<=\| )(?!_)([^|<>]+?) (□|■) 모름(?= \|)', r'<u>\1</u>  \2 모름', txt)

    # 정답지 뒷정리
    lines, name = [], None
    for l in txt.splitlines():
        mm = re.match(r'^\*\*카드 \d+ · (.+?)\*\*', l)
        if mm: name = mm.group(1)
        if name and '이름(또는 가족 내 호칭): ____________' in l:
            nm = re.match(r'(.+?)\s*\((.+?)\)', name)
            who, rel = (nm.group(1), nm.group(2)) if nm else (name, '')
            l = f"이름(또는 가족 내 호칭): {who.strip()}  관계: 이 사람은 그분의 {rel.split(',')[0].strip()}"
        lines.append(l)
    txt = re.sub(r'\n{3,}', '\n\n', "\n".join(lines)) + "\n"
    pathlib.Path(out).write_text(txt, encoding='utf-8')
    print(f"{out}: 문항 {len([b for b in blocks if b['id']])} / 답 채움 {len(used)} / 카드 {len(cards)}")
    miss = sorted(set(ans) - used)
    if miss: print("   시험지에 없는 답:", ", ".join(miss[:14]))

merge("Persona_Intake_시험지.md", sys.argv[1], sys.argv[2], sys.argv[3])
