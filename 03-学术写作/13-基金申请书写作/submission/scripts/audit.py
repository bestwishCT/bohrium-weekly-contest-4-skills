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
 d,p=load(); r=Report(); elements=['立项依据','科学问题','研究目标','创新点','技术路线','研究基础','可行性']; found={x['element'] for x in d['paragraphs']}
 for e in elements: r.need(e in found and e in d['manuscript'],'element:'+e,'七要素进入章节')
 for x in d['paragraphs']:
  tag=x['id']; r.need(x['element'] in elements,'element_type:'+tag,'段落要素类型有效')
  r.need(x['source_id'] in d['sources'] or x['kind']=='plan','paragraph_source:'+tag,'事实段有来源，计划段明示')
  if x['source_id'] in d['sources']:
   owner=d['sources'][x['source_id']]['owner']
   if owner=='external_author': r.need(not re.search(r'我们(?:已|团队)|申请人已',x['text']),'ownership:'+tag,'外部成果不归申请人')
 for ident in d['objective_ids']:
  for key in ('objectives','contents','routes','metrics'):
   r.need(ident in d[key],'matrix:'+key+':'+ident,'目标内容路线指标闭环')
 r.need(sum(x['months'] for x in d['phases'])<=d['constraints']['months'],'schedule','计划总时长符合约束')
 r.need('applicant_evidence' in d['gaps'],'applicant_gap','真实申请人证据缺失醒目')
 r.need(d['workorder_read'],'workorder','已读实际组织方工单')
 r.need(not re.search(r'国际领先|首次|填补空白',d['manuscript']),'unverified_innovation','不含无依据创新修饰')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
