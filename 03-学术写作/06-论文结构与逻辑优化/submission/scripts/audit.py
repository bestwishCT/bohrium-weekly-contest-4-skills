#!/usr/bin/env python3
import json, re, sys, hashlib
from pathlib import Path
from collections import Counter

class Report:
    def __init__(self): self.checks=[]
    def need(self, ok, code, message): self.checks.append({"code":code,"ok":bool(ok),"message":message})
    @property
    def failures(self): return [c for c in self.checks if not c["ok"]]

def numbers(text):
    return re.findall(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?", text or '')

def finish(data, path, report):
    out={"artifact":str(path),"status":"pass" if not report.failures else "fail","checks":report.checks}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if not report.failures else 1

def load():
    if len(sys.argv)<2: raise ValueError("usage: audit.py INPUT.json [render OUTPUT.svg]")
    p=Path(sys.argv[1]); data=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(data,dict): raise ValueError('top-level JSON must be object')
    return data,p

def main():
 d,p=load(); r=Report(); paras={x['id']:x for x in d['paragraphs']}; required=['研究问题','章节结构','论证链','段落关系','重复内容','结论边界']
 dims={x['dimension']:x for x in d['dimensions']}
 r.need(set(dims)==set(required),'six_dimensions','六维完整且不杜撰类别')
 for name in required:
  x=dims.get(name,{}); r.need(x.get('status') in ('checked_no_issue','found','unknown'),'status:'+name,'六维状态明确')
  if x.get('status')=='found':
   r.need(bool(x.get('actions')),'actions:'+name,'发现项有编辑动作')
   for a in x.get('actions',[]):
    r.need(a['anchor'] in paras,'anchor:'+name,'诊断锚点真实')
    if a['anchor'] in paras: r.need(a['excerpt'] in paras[a['anchor']]['text'],'excerpt:'+name,'诊断原文精确')
    r.need(bool(a['action'] and a['rationale'] and a['verify']),'executable:'+name,'动作/理由/复核可执行')
 for pair in d['similarity_candidates']:
  a,b=paras.get(pair['a']),paras.get(pair['b'])
  r.need(a is not None and b is not None,'similarity_ids','相似候选定位有效')
  r.need(pair['semantic_review'] in ('redundant','distinct','unknown'),'semantic_review:'+pair['a'],'相似度经语义复核')
 r.need(not d.get('full_rewrite',False),'no_unsolicited_rewrite','未主动全文重写')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
