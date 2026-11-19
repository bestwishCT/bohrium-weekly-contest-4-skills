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
 d,p=load(); r=Report(); entries={e['id']:e for e in d['entries']}
 body=' '.join(x['text'] for x in d['paragraphs'])
 cited=[]
 for x in d['paragraphs']:
  for n in re.findall(r'\[(\d+)\]',x['text']): cited.append(int(n))
 mapping={int(k):v for k,v in d['number_map'].items()}
 first=list(dict.fromkeys(cited))
 r.need(first==list(range(1,len(first)+1)),'contiguous_citations','首次正文编号连续且顺序正确；重复引用合法')
 r.need(set(mapping)==set(cited),'two_way_number_map','正文与列表双向覆盖')
 r.need(set(entries)==set(mapping.values()),'no_orphan_entries','文末无未引用孤条')
 for n,eid in mapping.items():
  r.need(eid in entries,'entry:'+str(n),'编号指向真实条目')
 original_ids={e['id'] for e in d['original_entries']}
 for g in d['dedup_groups']:
  ids=g['members']; r.need(g['kept'] in ids,'dedup_kept:'+g['kept'],'去重保留项属于组成员')
  for eid in ids: r.need(eid in original_ids,'dedup_original:'+eid,'合并项来自原库')
  r.need(bool(g['reason']),'dedup_reason:'+g['kept'],'去重理由可解释')
 dois=[e['doi'].lower().strip() for e in d['entries'] if e.get('doi')]
 r.need(len(dois)==len(set(dois)),'no_duplicate_doi','保留条目无重复DOI')
 for e in d['support']:
  r.need(e['entry_id'] in entries and e['status'] in ('支持','部分支持','不支持','无法判断'),'support:'+e['claim_id'],'论断有合法支撑判定')
  r.need(bool(e['locator']),'support_locator:'+e['claim_id'],'支撑定位非空')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
