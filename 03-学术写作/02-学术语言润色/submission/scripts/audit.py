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
 d,p=load(); r=Report(); a,b=d['original'],d['polished']
 r.need(bool(b.strip()),'complete_text','完整润色文本非空')
 r.need(Counter(numbers(a))==Counter(numbers(b)),'numbers_conserved','数字多重集保持')
 for tok in d['protected_tokens']:
  r.need(a.count(tok)==b.count(tok),'protected:'+tok,'引用/公式/图表标记保持')
 for tok in d['semantic_tokens']:
  r.need((tok in a)==(tok in b),'semantic:'+tok,'限定或否定范围未漂移')
 ids=[e['sentence_id'] for e in d['edits']]
 r.need(len(ids)==len(set(ids)),'unique_edit_ids','句ID唯一')
 for e in d['edits']:
  tag=e['sentence_id']; r.need(e['before'] in a,'before:'+tag,'原文锚点有效')
  r.need(e['after'] in b,'after:'+tag,'改文进入完整文本')
  r.need(bool(e['type'] and e['reason']),'reason:'+tag,'编辑类型和具体理由存在')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
