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
 d,p=load(); r=Report(); mats={m['id']:m for m in d['materials']}
 r.need(len(mats)==len(d['materials']),'unique_material_ids','材料ID不得重复')
 text=d['manuscript']; heads=set(re.findall(r'^#{1,3}\s+(.+)$',text,re.M))
 for sec in d['required_sections']: r.need(sec in heads,'section:'+sec,'必需章节存在')
 for e in d['evidence']:
  src=mats.get(e['source_id']); tag=e['claim_id']
  r.need(src is not None,'source:'+tag,'论断来源存在')
  if src:
   r.need(str(e['value']) in src['excerpt'],'source_value:'+tag,'数值存在于来源摘录')
   r.need(str(e['value']) in text,'manuscript_value:'+tag,'数值存在于正文')
   r.need(bool(e['locator'].strip()),'locator:'+tag,'来源定位非空')
 for f in d['figures']: r.need(f in text,'figure:'+f,'图表指向已转写')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
