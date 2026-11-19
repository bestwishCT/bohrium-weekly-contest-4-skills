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
 d,p=load(); r=Report(); qs=d['research_questions']
 r.need(1<=len(qs)<=3,'question_count','研究问题为一至三个')
 for q in qs:
  for key in ('objectives','routes','validations','outcomes'): r.need(q in d[key],'mapping:'+key+':'+q,'问题目标路线验证成果闭环')
 for sec in ['研究背景','问题定义','研究目标','技术路线','阶段计划','风险','预期成果']: r.need(sec in d['manuscript'],'section:'+sec,'七要素章节存在')
 periods={x['id']:x for x in d['schedule']}
 for x in d['schedule']:
  r.need(x['end']<=d['months'],'period:'+x['id'],'任务落在给定周期')
  r.need(x['start']<=x['end'],'period_order:'+x['id'],'任务起止顺序正确')
  for dep in x.get('depends',[]): r.need(dep in periods and periods[dep]['end']<x['start'],'dependency:'+x['id'],'前置依赖先完成')
 r.need(sum(d['costs'])<=d['budget'],'budget','预算总和未超限')
 for x in d['risks']:
  r.need(all(x.get(k) for k in ('trigger','impact','fallback','decision_by')),'risk:'+x['id'],'风险触发影响备选决策点齐全')
 for ident,x in d['thresholds'].items(): r.need(x.get('source') or x.get('tentative'),'threshold:'+ident,'阈值有来源或标待定')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
