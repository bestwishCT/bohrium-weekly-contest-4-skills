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

def strip_comments(text): return '\n'.join(re.sub(r'(?<!\\)%.*','',x) for x in text.splitlines())

def main():
 d,p=load(); r=Report(); root=(p.parent/d['main']).resolve(); text=strip_comments(root.read_text(encoding='utf-8'))
 r.need('\\documentclass' in text and '\\begin{document}' in text and '\\end{document}' in text,'entry_structure','主文件结构完整')
 begins=re.findall(r'\\begin\{([^}]+)\}',text); ends=re.findall(r'\\end\{([^}]+)\}',text)
 r.need(Counter(begins)==Counter(ends),'environment_balance','环境begin/end多重集一致')
 stack=[]; nesting_ok=True
 for kind,env in re.findall(r'\\(begin|end)\{([^}]+)\}',text):
  if kind=='begin': stack.append(env)
  elif not stack or stack.pop()!=env: nesting_ok=False
 r.need(nesting_ok and not stack,'environment_nesting','环境嵌套和闭合顺序正确')
 depth=0; braces_ok=True
 for token in re.findall(r'(?<!\\)[{}]',text):
  depth+=1 if token=='{' else -1
  if depth<0: braces_ok=False
 r.need(braces_ok and depth==0,'brace_balance','未转义花括号配对')
 dollars=len(re.findall(r'(?<!\\)\$',text)); r.need(dollars%2==0,'dollar_math_balance','行内/显示美元定界符配对')
 r.need(text.count('\\[')==text.count('\\]') and text.count('\\(')==text.count('\\)'),'slash_math_balance','LaTeX数学定界符配对')
 for env in ['document']:
  r.need(text.find('\\begin{'+env+'}')<text.find('\\end{'+env+'}'),'environment_order:'+env,'环境顺序正确')
 label_list=re.findall(r'\\label\{([^}]+)\}',text); labels=set(label_list)
 r.need(len(label_list)==len(labels),'unique_labels','label不得重复')
 refs=set(re.findall(r'\\(?:ref|eqref)\{([^}]+)\}',text))
 for x in refs-labels: r.need(False,'undefined_label:'+x,'交叉引用有label')
 keys=set(re.findall(r'@[A-Za-z]+\s*\{\s*([^,\s]+)',(p.parent/d['bib']).read_text(encoding='utf-8')))
 cites=set(k.strip() for group in re.findall(r'\\cite\w*\{([^}]+)\}',text) for k in group.split(','))
 for x in cites-keys: r.need(False,'undefined_citation:'+x,'引文键存在bib条目')
 for inc in re.findall(r'\\(?:input|include)\{([^}]+)\}',text):
  q=root.parent/(inc if inc.endswith('.tex') else inc+'.tex'); r.need(q.exists(),'input_exists:'+inc,'include/input文件真实存在')
 for image in re.findall(r'\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}',text):
  q=root.parent/image
  candidates=[q] if q.suffix else [q.with_suffix(ext) for ext in ('.pdf','.png','.jpg','.jpeg','.eps')]
  r.need(any(x.is_file() for x in candidates),'graphic_exists:'+image,'includegraphics图片真实存在')
 r.need(d['build']['status'] in ('success','failed','blocked','not_run'),'build_status_honest','构建状态为真实枚举')
 if d['build']['status']=='success':
  r.need(bool(d['build']['command']) and d['build']['exit_code']==0,'build_command','成功必须有实际命令及0退出')
  pdf=p.parent/d['build']['pdf']; r.need(pdf.is_file() and pdf.read_bytes().startswith(b'%PDF-'),'real_pdf','成功记录对应真实PDF签名文件')
  log=p.parent/d['build']['execution_log']; r.need(log.is_file() and len(log.read_text(encoding='utf-8',errors='replace').strip())>0,'real_build_log','成功记录附真实非空构建日志')
 else: r.need(bool(d['build'].get('reason')),'build_reason','非成功说明未构建原因')
 return finish(d,p,r)

if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"status":"error","error":str(exc)},ensure_ascii=False))
        sys.exit(2)
