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
 d,p=load(); r=Report(); ids=[x['id'] for x in d['source_segments']]; targets=d['target_segments']
 r.need(d['language_pair'] in ('en-zh','zh-en'),'supported_language_pair','仅声明中英互译')
 r.need(list(targets)==ids,'segment_alignment','句段ID、顺序和数量一一对应')
 for i,s in enumerate(d['source_segments']):
  t=targets.get(s['id'],''); tag=s['id']
  r.need(bool(t.strip()),'nonempty:'+tag,'目标段非空且未漏译')
  r.need(Counter(numbers(s['text']))==Counter(numbers(t)),'numbers:'+tag,'数字多重集保持')
  for tok in re.findall(r'\[[0-9,\-]+\]|\$[^$]+\$',s['text']): r.need(tok in t,'protected:'+tag+':'+tok,'公式/引用原位保持')
  for tok in d['protected_tokens']:
   if tok in s['text']: r.need(tok in t,'protected_entity:'+tag+':'+tok,'锁定不可变量保持')
 for term in d['terms']:
  for s in d['source_segments']:
   if term['source'] in s['text']:
    r.need(term['target'] in targets.get(s['id'],''),'term:'+s['id']+':'+term['source'],'术语译法锁定')
 r.need(d['output_form'] in ('translation','bilingual','tracked'),'output_form','输出形态明示且合法')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
