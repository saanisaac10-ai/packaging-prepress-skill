import pikepdf
PLAIN_OPS={'m','l','c','v','y','h','re','f','f*','F','B','B*','b','b*','S','s','n','W','W*','cm','q','Q','scn','sc','SCN','SC','cs','CS','g','G','rg','RG','k','K','w','J','j','M','d','ri','i'}
def split_blocks(stream_obj):
    ops=pikepdf.parse_content_stream(stream_obj)
    blocks=[]; cur=None; depth=0; loose=[]
    for operands,op in ops:
        o=str(op)
        if o=='q':
            if depth==0: cur=[]
            depth+=1
        if depth==0: loose.append((operands,op)); continue
        cur.append((operands,op))
        if o=='Q':
            depth-=1
            if depth==0: blocks.append(cur); cur=None
    return blocks, loose
def form_has(xo, pred, seen=None):
    seen=seen or set()
    if xo.objgen in seen: return False
    seen.add(xo.objgen)
    r=xo.get('/Resources',{})
    for g in r.get('/ExtGState',{}).values():
        if pred(g): return True
    for sub in r.get('/XObject',{}).values():
        if sub.get('/Subtype')=='/Form' and form_has(sub,pred,seen): return True
    return False
def classify(block, res):
    kinds=set(); dos=[]; gss=[]
    for operands,op in block:
        o=str(op)
        if o not in PLAIN_OPS: kinds.add(o)
        if o=='Do': dos.append(str(operands[0]))
        if o=='gs': gss.append(str(operands[0]))
    complex_=bool(kinds)
    return complex_, kinds, dos, gss
