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
 d,p=load(); r=Report(); panels={x['id']:x for x in d['panels']}; findings={x['id']:x for x in d['findings']}
 for x in d['readings']:
  tag=x['id']; src=panels.get(x['panel_id'])
  r.need(src is not None,'panel:'+tag,'读数面板存在')
  if src:
   r.need(x['value']==src['value'] and x['unit']==src['unit'],'reading:'+tag,'读数与精确表值/单位一致')
   r.need(x['label'] in d['results'],'results_value:'+tag,'读数转写进结果全文')
 for f in d['findings']:
  r.need(f['sentence'] in d['results'],'finding:'+f['id'],'主要/阴性/不一致发现入结果')
  if f.get('significant') is False:
   r.need(not re.search(r'显著(?:提高|降低|改善)|证明无效',f['sentence']),'significance:'+f['id'],'不显著不被改写为显著或无效')
 r.need(any(f['kind']=='negative' for f in d['findings']),'negative_coverage','阴性结果未隐藏')
 for c in d['discussion_claims']:
  r.need(c['sentence'] in d['discussion'],'discussion:'+c['id'],'讨论论断进入全文')
  r.need(c['finding_id'] in findings or bool(c.get('literature_locator')),'discussion_source:'+c['id'],'讨论绑定发现或文献')
  if c['kind']=='mechanism': r.need(any(w in c['sentence'] for w in ('可能','推测')),'mechanism:'+c['id'],'机制标为解释/推测')
 r.need(d['design']!='observational' or not re.search(r'导致|证明',d['discussion']),'causal_boundary','观察研究讨论不作因果')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
