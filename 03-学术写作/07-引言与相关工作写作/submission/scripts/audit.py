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
 d,p=load(); r=Report(); refs={x['id']:x for x in d['literature']}; text=d['manuscript']
 for s in ['研究背景','相关工作','研究空白','贡献']:
  r.need(s in text,'section:'+s,'四部分成稿存在')
 for l in d['literature']:
  r.need(bool(l['author'] and l['year'] and l['venue']),'metadata:'+l['id'],'作者年份来源齐备')
 for g in d['groups']:
  r.need(len(g['members'])>0 and all(x in refs for x in g['members']),'group:'+g['route'],'路线分组引用有效')
  r.need(bool(g['comparison']),'comparison:'+g['route'],'同维度比较非逐篇摘要')
 for g in d['gaps']:
  r.need(all(x in refs for x in g['limitations_from']),'gap_sources:'+g['id'],'空白由真实文献局限推出')
  r.need(bool(g['response_anchor']),'gap_response:'+g['id'],'空白与本文响应对齐')
 for c in d['contributions']:
  r.need(c['evidence_anchor'] in d['paper_evidence'],'contribution:'+c['id'],'贡献有论文实际锚点')
  r.need(c['statement'] in text,'contribution_text:'+c['id'],'贡献进入成稿')
 for claim in d['claims']:
  r.need(all(x in refs for x in claim['source_ids']),'citation_coverage:'+claim['id'],'关键论断引用有效')
 r.need(not re.search(r'首次|国际领先|尚无研究',text),'unverified_novelty','不含无依据首创/空白宣称')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
