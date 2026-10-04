"""Build only three diagnostic objects, reusing the deployed CUDA libraries.
No writes to the production source/build/binary. Run on Song PC, under direct-env.sh.
"""
import argparse,json,shlex,shutil,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
# Reusing old libraries across an upstream upgrade can link incompatible buffer layouts.
# Only the three instrumented translation units and the timeline header may differ.
root = Path(__file__).resolve().parents[1]
allowed = {'src/core/verify.cpp', 'src/core/expert_source.cpp', 'src/program/generate.cpp',
           'include/strata/core/window_timeline.hpp'}
paths = ['CMakeLists.txt'] + [str(p.relative_to(root)) for folder in ('include', 'src', 'cmake')
                             for p in (root / folder).rglob('*') if p.is_file()]
for rel in paths:
    if rel in allowed:
        continue
    base_file = a.base / rel
    if not base_file.is_file() or base_file.read_bytes() != (root / rel).read_bytes():
        raise SystemExit('Library reuse refused: base differs at ' + rel +
                         '. Configure and build the entire checkout with CMake instead.')
build=a.base/'build';out=a.out.resolve();ninja=a.base/'.venv/bin/ninja'
commands=subprocess.check_output([str(ninja),'-C',str(build),'-t','commands','strata'],text=True).splitlines()
objects={}
for name in ('src/core/verify.cpp','src/core/expert_source.cpp','src/program/generate.cpp'):
    matches=[line for line in commands if (' -c '+str(a.base/name)) in line]
    assert len(matches)==1,(name,len(matches))
    args=shlex.split(matches[0]);obj=out/(Path(name).name+'.o');new=[args[0],'-I'+str(out/'include')]
    i=1
    while i<len(args):
        if args[i] in ('-MT','-MF'):i+=2;continue
        if args[i]=='-MD':i+=1;continue
        if args[i]=='-o':new+=['-o',str(obj)];i+=2;continue
        if args[i]=='-c':new+=['-c',str(out/name)];i+=2;continue
        new.append(args[i]);i+=1
    print('Compile',name,flush=True);subprocess.run(new,cwd=build,check=True);objects[name]=obj
archive=out/'libstrata_engine.a';shutil.copy2(build/'libstrata_engine.a',archive)
subprocess.run(['/usr/bin/ar','r',str(archive),str(objects['src/core/verify.cpp']),str(objects['src/core/expert_source.cpp'])],check=True)
link=shlex.split(commands[-1]);link=link[link.index('/usr/bin/c++'):];link=link[:link.index('&&')] if '&&' in link else link
for i,v in enumerate(link):
    if v=='libstrata_engine.a':link[i]=str(archive)
    elif v.endswith('src/program/generate.cpp.o'):link[i]=str(objects['src/program/generate.cpp'])
    elif v=='-o':link[i+1]=str(out/'strata-timeline')
print('Link isolated diagnostic engine',flush=True);subprocess.run(link,cwd=build,check=True)
(out/'build-provenance.json').write_text(json.dumps({'base_revision':subprocess.check_output(['git','-C',str(a.base),'rev-parse','HEAD'],text=True).strip(),'commands':commands[-1:],'diagnostic_binary':str(out/'strata-timeline'),'reused':'Unmodified deployed CUDA libraries; only verify, expert_source, generate rebuilt'},indent=2))
