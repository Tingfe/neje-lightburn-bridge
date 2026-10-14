"""Offline IL evidence extractor for the publicly downloaded NEJE executable.
Requires dnfile/dncil in a separate analysis directory; never executes the assembly.
"""
import json
import logging
from pathlib import Path
import sys
sys.path.insert(0, '/private/tmp/neje-analysis-libs')
import dnfile
from dncil.cil.body.reader import read_method_body_from_bytes
from dncil.clr.token import Token
logging.getLogger('dnfile').setLevel(logging.ERROR)
ROOT = Path(__file__).resolve().parents[1]
p = dnfile.dnPE(str(ROOT / 'evidence/grbl-research/NEJE_v6.8.5.exe'))
owners = {}
for t in p.net.mdtables.TypeDef:
    owner = str(t.TypeNamespace)+'.'+str(t.TypeName)
    for attr in ('MethodList','FieldList'):
        for entry in getattr(t,attr):
            owners[id(entry.row)] = owner

def resolve(value):
    if not isinstance(value, Token):
        return str(value)
    token = value.value
    table, index = token >> 24, token & 0xffffff
    if table == 0x70:
        return repr(p.net.user_strings.get(index).value)
    tables={2:'TypeDef',4:'Field',6:'MethodDef',10:'MemberRef',1:'TypeRef',17:'StandAloneSig',27:'TypeSpec',43:'MethodSpec'}
    try:
        row = getattr(p.net.mdtables,tables[table]).rows[index-1]
        label = str(getattr(row,'Name',getattr(row,'TypeName','')))
        owner=owners.get(id(row),'')
        if table==10:
            parent=row.Class.row
            owner=owners.get(id(parent),'') or (str(getattr(parent,'TypeNamespace',''))+'.'+str(getattr(parent,'TypeName','')))
        elif table==43:
            method=row.Method.row
            label=str(method.Name)
            parent=getattr(method,'Class',None)
            if parent:
                owner=str(getattr(parent.row,'TypeNamespace',''))+'.'+str(getattr(parent.row,'TypeName',''))
        return f'{token:08x} '+owner+'::'+label
    except Exception:
        return f'{token:08x}'

def main():
    names = ['NEJE.Commands','NEJE.CommandResolver','NEJE.GrblCommWorker','NEJE.ConnHelper',
             '.<Start>d__21','.<Stop>d__22','.<TaskRun>d__27','.<ReadGrblCommStatus>d__12',
             'Library.Helper.CheckHelper']
    out = ROOT / 'evidence/grbl-research/il'
    out.mkdir(exist_ok=True)
    index=[]
    for number, row in enumerate(p.net.mdtables.MethodDef,1):
        owner=owners.get(id(row),'')
        if not row.Rva:continue
        try:
            body=read_method_body_from_bytes(p.get_data(row.Rva, 100000))
        except Exception:
            continue
        calls = [ins.operand.value for ins in body.instructions if isinstance(ins.operand,Token)
                 and ins.opcode.name in ('call','callvirt')]
        if owner not in names and 'SwitchAppWise' not in owner and not any(
            token in calls for token in (0x060000cc,0x060001a9,0x0600007d)):
            continue
        lines=[f'{owner}::{row.Name} token=0600{number:04x} RVA={row.Rva:08x}',
               f'signature={row.Signature.value.hex()}']
        for ins in body.instructions:
            lines.append(f'{ins.offset:04x} {ins.opcode.name:16s} {resolve(ins.operand) if ins.operand is not None else ""}')
        name=f'{number:04d}-{str(row.Name).replace(".","_")}.il.txt'
        (out/name).write_text('\n'.join(lines)+'\n')
        index.append({'token':f'{0x06000000+number:08x}','owner':owner,'method':str(row.Name),'file':name,'rva':row.Rva})
    (out/'index.json').write_text(json.dumps(index,indent=2)+'\n')
    print('Saved',len(index),'methods')

if __name__=='__main__':main()
