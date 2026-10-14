// Offline differential check against archived official public JS. No Bluetooth APIs.
import fs from 'node:fs';
const base = new URL('../evidence/web/', import.meta.url);
const urls = new Map();
function moduleURL(name) {
  if (urls.has(name)) return urls.get(name);
  const source = fs.readFileSync(new URL(name, base), 'utf8').replace(
    /from ['"]\.\/([A-Za-z0-9_-]+\.js)['"]/g,
    (_, dependency) => 'from "'+moduleURL(dependency)+'"');
  const url = 'data:text/javascript;base64,'+Buffer.from(source).toString('base64');
  urls.set(name, url);
  return url;
}
const {Commands} = await import(moduleURL('Commands.js'));
const {CheckHelper} = await import(moduleURL('Tool.js'));
const {CommandPicker} = await import(moduleURL('CommandPicker.js'));
const rows = fs.readFileSync(new URL('../logs/p0-official-init-host.jsonl',import.meta.url),'utf8').trim().split('\n').map(JSON.parse);
const trials = [];
for (const trial of [1,2]) {
  const frames = [];
  const picker = new CommandPicker({ResolveCommand(data) {frames.push(Buffer.from(data).toString('hex'));}});
  for (const row of rows.filter(r=>r.event==='notify' && r.trial===trial)) {
    picker.ProcessNewData(Array.from(Buffer.from(row.hex.replaceAll(' ',''),'hex')));
  }
  const pythonFrames=rows.filter(r=>r.event==='frame' && r.trial===trial).map(r=>r.hex.replaceAll(' ',''));
  if (JSON.stringify(frames)!==JSON.stringify(pythonFrames)) throw Error('Decoder mismatch');
  trials.push({trial,official_frames:frames.length,python_frames:pythonFrames.length,match:true,pending_bytes:picker.data.length});
}
console.log(JSON.stringify({offline_only:true,verify:Buffer.from(Commands.verify).toString('hex'),
  system_read_all:Buffer.from(Commands.SystemRead(255)).toString('hex'),crc_check:CheckHelper.CRC32(Buffer.from('123456789')).toString(16),trials},null,2));
