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
 d,p=load(); r=Report(); base=d['baseline']; revised=d['revised']; comments={x['id']:x for x in d['subrequests']}; responses={x['subrequest_id']:x for x in d['responses']}
 r.need(set(comments)==set(responses),'subrequest_coverage','每条子要求均有回复')
 r.need(d['scope']=='controlled_baseline_summary','controlled_scope','只处理受控基线摘要')
 for x in d['responses']:
  tag=x['subrequest_id']; r.need(x['decision'] in ('采纳','部分采纳','不采纳'),'decision:'+tag,'处理决策明确')
  r.need(bool(x['reply']),'reply:'+tag,'逐条回复非空')
  if x['decision']!='不采纳':
   for a in x['anchors']:
    r.need(a['before'] in base,'before_anchor:'+tag,'改前锚点位于基线')
    r.need(a['after'] in revised,'after_anchor:'+tag,'声称改动已在修订稿')
    r.need(a['after'] in x['reply'] or a['label'] in x['reply'],'reply_anchor:'+tag,'回复定位可反查')
  if x['evidence_status']=='未到位':
   r.need('未到位' in x['reply'],'evidence_honesty:'+tag,'未到位证据不虚称已补')
   r.need(tag in d['missing_evidence'],'missing_list:'+tag,'未到位证据入清单')
   r.need(not re.search(r'已补充实验|已收到|已接收|全文返修完成',x['reply']),'no_false_completion:'+tag,'无实验/编辑/全文虚假状态')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
