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
 d,p=load(); r=Report(); src={s['id']:s for s in d['sources']}; ev={e['id']:e for e in d['evidence']}
 for e in d['evidence']:
  s=src.get(e['source_id']); tag=e['id']
  r.need(s is not None,'evidence_source:'+tag,'证据文献存在')
  if s:
   r.need(e['quote'] in s['text'],'exact_quote:'+tag,'摘录逐字位于实际层级文本')
   r.need(s['level'] in ('abstract','fulltext'),'source_level:'+tag,'证据层级明示')
   r.need(bool(e['locator']),'evidence_locator:'+tag,'证据定位非空')
 sentence_ids=[c['sentence_id'] for c in d['claims']]
 r.need(len(sentence_ids)==len(set(sentence_ids)) and all(sentence_ids),'unique_sentence_ids','稳定sentence_id唯一非空')
 for c in d['claims']:
  tag=c['id']; sentence=c['sentence']
  r.need(d['manuscript'].count(sentence)==1,'claim_sentence:'+tag,'完整句在成稿中唯一定位')
  r.need(all(x in ev for x in c['evidence_ids']),'claim_evidence:'+tag,'论断证据ID存在')
  linked=set(re.findall(r'\[(EV[-_A-Za-z0-9]+)\]',sentence))
  r.need(linked==set(c['evidence_ids']),'sentence_binding:'+tag,'具体句子与证据ID双向绑定')
  r.need(all(tag in ev[x].get('claim_ids',[]) and c['id'] in ev[x].get('claim_ids',[]) for x in c['evidence_ids'] if x in ev),'evidence_backlink:'+tag,'每条证据反向指向该论断')
  r.need(all(ev[x]['support'] in ('支持','部分支持','不支持','无法判断') for x in c['evidence_ids'] if x in ev),'support_status:'+tag,'绑定证据有支持范围判定')
  r.need(bool(c['strength_reason'] and c['uncertainty']),'strength_reason:'+tag,'强度和不确定性有理由')
  if c['design']=='observational':
   r.need(not re.search(r'导致|证明|causes?|proves?',sentence,re.I),'causal_boundary:'+tag,'观察设计不作因果断言')
 reverse_claims={cid for e in d['evidence'] for cid in e.get('claim_ids',[])}
 r.need(reverse_claims=={c['id'] for c in d['claims']},'reverse_map_complete','证据→论断映射无孤条或未知论断')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
