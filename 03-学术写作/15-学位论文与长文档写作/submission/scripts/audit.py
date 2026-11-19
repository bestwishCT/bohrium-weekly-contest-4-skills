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
 d,p=load(); r=Report(); chapters={x['id']:x for x in d['chapters']}; order=d['workorder']['targets']
 r.need(set(order)<=set(chapters),'workorder_targets','工单目标均在真实工作章节')
 r.need(d['workorder']['read'],'workorder_read','已读取实际组织方编辑工单')
 for x in d['edits']:
  tag=x['id']; r.need(x['chapter'] in order,'scope:'+tag,'只编辑工单指定章节')
  if x['chapter'] in chapters:
   r.need(x['before'] in chapters[x['chapter']]['text'],'before:'+tag,'原句锚点在对应章节')
   r.need(x['after'] in chapters[x['chapter']]['revised'],'after:'+tag,'改句真实落入修订章')
 for term in d['terms']:
  canon=term['canonical']; affected=d['term_impacts'].get(canon,[])
  r.need(all(x in chapters for x in affected),'impact_scope:'+canon,'术语影响集定位真实章节')
  for ch in order:
   if ch in affected: r.need(canon in chapters[ch]['revised'],'impact_applied:'+canon+':'+ch,'范围内跨章同步完成')
 for ch in order:
  for variant in [v for t in d['terms'] for v in t['variants']]: r.need(variant not in chapters[ch]['revised'],'variant:'+ch+':'+variant,'目标章不再混用旧术语')
 r.need(len(d['chapter_states'])==len(chapters),'chapter_state_coverage','所有原工作章节有状态摘要')
 r.need(not d.get('completed_thesis_claim',False),'scope_claim','未冒称完整学位论文完成')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
