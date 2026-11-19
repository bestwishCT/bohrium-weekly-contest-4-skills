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
 d,p=load(); r=Report(); src={u['id']:u for u in d['source_units']}; out={u['id']:u for u in d['converted_units']}
 r.need(set(src)==set(out),'content_unit_ids','内容单元ID无遗漏')
 for k,v in src.items():
  if k in out:
   digest=lambda x:hashlib.sha256(x['text'].encode()).hexdigest()
   r.need(digest(v)==digest(out[k]),'content_hash:'+k,'转换前后文字不变')
 for s in d['specs']:
  tag=s['key']; r.need(bool(s['source']),'spec_source:'+tag,'规格有模板来源')
  r.need(d['applied'].get(tag)==s['value'],'spec_applied:'+tag,'转换应用值与规格一致')
 r.need(d['font']['family']=='Liberation Serif','required_font','revision3使用Liberation Serif')
 r.need(d['font']['revision']==3,'font_revision','字体需求为revision3')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
