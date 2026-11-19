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
 d,p=load(); r=Report(); facts={x['id']:x for x in d['source_facts']}
 for out in d['transformations']:
  tag=out['audience']; text=out['text']
  r.need(all(out['profile'].get(k) for k in ('knowledge','purpose','genre')),'profile:'+tag,'受众画像完整')
  r.need(len(text)<=out['max_chars'],'length:'+tag,'转化稿不超篇幅')
  mapped={x['fact_id']:x for x in out['mapping']}
  for fid,f in facts.items():
   if f['required']:
    r.need(fid in mapped,'mapping:'+tag+':'+fid,'必留事实具有取舍记录')
    if fid in mapped:
     r.need(mapped[fid]['action'] in ('keep','compress'),'required_kept:'+tag+':'+fid,'必留证据未删除')
     r.need(f['token'] in text,'fact_token:'+tag+':'+fid,'关键数值/限定保留')
     r.need(bool(mapped[fid]['reason']),'reason:'+tag+':'+fid,'逐段取舍有理由')
  r.need(not re.search(r'国际领先|保证治愈|市场效益显著',text),'no_new_claims:'+tag,'无源外宣传或效益')
 r.need(len({x['audience'] for x in d['transformations']})==len(d['transformations']),'unique_audiences','各受众独立产物')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
