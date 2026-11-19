#!/usr/bin/env python3
import json, re, sys, hashlib
from pathlib import Path
from collections import Counter

class Report:
    def __init__(self): self.checks=[]
    def need(self, ok, code, message): self.checks.append({"code":code,"ok":bool(ok),"message":message})
    def pending(self, code, message):
        self.checks.append({"code":code,"ok":False,"status":"pending","message":message})
    @property
    def failures(self): return [c for c in self.checks if not c["ok"] and c.get("status")!="pending"]
    @property
    def pending_checks(self): return [c for c in self.checks if c.get("status")=="pending"]

def numbers(text):
    return re.findall(r"(?<![A-Za-z])[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?%?", text or '')

def finish(data, path, report, resolutions, manual_confirmations):
    status="fail" if report.failures else ("partial" if report.pending_checks else "pass")
    out={"artifact":str(path),"status":status,"checks":report.checks,
         "priority_resolutions":resolutions,"manual_confirmations":manual_confirmations}
    print(json.dumps(out,ensure_ascii=False,indent=2))
    return 0 if status=="pass" else 1

def conflict_record(raw):
    # Legacy arrays identify candidate values only; they never supply case priority.
    return raw if isinstance(raw,dict) else {"values":raw,"affected_steps":[]}

def priority_is_explicit(priority, case_id):
    if not isinstance(priority,dict): return False
    order=priority.get('order'); basis=priority.get('basis')
    if not isinstance(order,list) or not order: return False
    if any(not isinstance(kind,str) or not kind for kind in order): return False
    if len(set(order))!=len(order): return False
    if not isinstance(basis,dict): return False
    return (basis.get('case_id')==case_id and isinstance(case_id,str) and bool(case_id)
            and all(isinstance(basis.get(k),str) and basis[k].strip()
                    for k in ('locator','statement')))

def load():
    if len(sys.argv)<2: raise ValueError("usage: audit.py INPUT.json [render OUTPUT.svg]")
    p=Path(sys.argv[1]); data=json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(data,dict): raise ValueError('top-level JSON must be object')
    return data,p

def main():
 d,p=load(); r=Report(); resolutions=[]; manual=[]; allowed=['方法步骤','实验设置','参数','数据集','对照实验','可复现性与限制条件']
 steps={x['id']:x for x in d['steps']}
 for s in allowed: r.need(s in d['manuscript'],'section:'+s,'六类章节存在')
 for x in d['parameters']:
  tag=x['name']; observed=d['sources'][x['source_kind']].get(tag)
  r.need(observed==x['value'],'parameter_source:'+tag,'参数与指定类别来源一致')
  if tag in d['conflicts']:
   conflict=conflict_record(d['conflicts'][tag]); affected=conflict.get('affected_steps',[])
   priority=conflict.get('case_priority'); code='parameter_conflict:'+tag
   if not isinstance(affected,list) or any(not isinstance(step,str) or step not in steps for step in affected):
    r.need(False,'conflict_steps:'+tag,'受影响步骤必须是已登记步骤ID列表')
    affected=[]
   if priority is None:
    r.pending(code,'case未提供明确兼容的来源优先级；待人工确认，未自动选择日志或其他来源')
    manual.append({"parameter":tag,"code":code,"status":"pending",
                   "reason":"case未提供明确兼容的来源优先级",
                   "candidates":conflict.get('values',[]),"affected_steps":affected,
                   "selected_source_kind":None,"selected_value":None})
   else:
    order=priority.get('order',[]) if isinstance(priority,dict) else []
    winner=order[0] if order and isinstance(order[0],str) else None
    compatible=(priority_is_explicit(priority,d.get('case_id')) and winner in d['sources']
                and tag in d['sources'][winner] and x['source_kind']==winner
                and x['value']==d['sources'][winner][tag])
    r.need(compatible,code,'选定参数必须符合当前case明示且兼容的来源优先级')
    if compatible:
     resolutions.append({"parameter":tag,"selected_source_kind":winner,
                         "selected_value":x['value'],"priority_order":order,
                         "priority_basis":priority['basis'],"affected_steps":affected})
 counts=d['dataset']['split_counts']; ratios=d['dataset']['split_ratios']
 r.need(sum(counts.values())==d['dataset']['total'],'dataset_total','数据划分数量加和正确')
 r.need(abs(sum(ratios.values())-1.0)<1e-9,'dataset_ratio','划分比例加和为1')
 for x in d['steps']:
  r.need(all(k in x for k in ('input','operation','output')),'step_fields:'+x['id'],'步骤输入操作输出完整')
  r.need(all(dep in steps for dep in x.get('depends',[])),'step_dependencies:'+x['id'],'步骤依赖有效')
 r.need(len(d['reproduction_gaps'])>0 or d['reproduction_complete'],'reproduction_honesty','复现缺件明示')
 r.need(not d.get('commands_executed',False),'no_unrun_claim','本审计未声称实际执行材料命令')
 return finish(d,p,r,resolutions,manual)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
