// Offline reference packet generation: layout transcribed from inspected Windows IL;
// CRC executed by the archived official JS helper. No hardware or assembly execution.
import fs from 'node:fs';
const source=fs.readFileSync(new URL('../evidence/web/Tool.js',import.meta.url),'utf8');
const {CheckHelper}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
function packet(command,seq,payload=Buffer.alloc(0)) {
  const buf=Buffer.alloc(payload.length+9);
  buf[0]=255;buf[1]=170;buf[2]=buf.length;buf[3]=64;buf[4]=command;
  buf.writeUInt16LE(seq,6);payload.copy(buf,8);
  buf[buf.length-1]=CheckHelper.CRC32(buf,1,buf.length-2)&255;
  return buf.toString('hex');
}
const result={provenance:'Windows v6.8.5 IL layout + archived official Web CRC; offline only',
  build_at_0:packet(3,0,Buffer.from('$I\n')),
  build_at_65535:packet(3,65535,Buffer.from('$I\n')),
  status_at_3:packet(3,3,Buffer.from('?')),
  read_from_4660:packet(4,4660)};
console.log(JSON.stringify(result,null,2));
