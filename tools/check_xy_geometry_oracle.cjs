// Offline independent geometry reference. This never authorizes or sends GCode.
const fs = require('node:fs');
const path = require('node:path');
const Toolpath = require(path.resolve(__dirname, '../evidence/xy-linear/open-source/node_modules/gcode-toolpath'));
const files = process.argv.slice(2);
if (!files.length) throw Error('Pass one or more files for offline geometry parsing');
(async () => {
  const output = [];
  for (const file of files) {
    const segments = [], arcs = [];
    const toolpath = new Toolpath({
      position: {x: 0, y: 400, z: 0},
      modal: {motion: 'G0', units: 'G21', distance: 'G90', plane: 'G17', spindle: 'M5'},
      addLine: (modal, from, to) => {
        const length = Math.hypot(to.x-from.x, to.y-from.y, to.z-from.z);
        if (length) segments.push({motion: modal.motion, from, to, length});
      },
      addArcCurve: (modal, from, to, center) => arcs.push({motion: modal.motion, from, to, center})
    });
    await new Promise((resolve, reject) => toolpath.loadFromString(fs.readFileSync(file, 'utf8'),
      error => error ? reject(error) : resolve()));
    const vertices = [{x:0,y:400,z:0}, ...segments.map(segment=>segment.to)];
    output.push({file, reference: 'cncjs/gcode-toolpath@3.0.0', offline_only:true,
      vertices, segments, arcs, line_distance_mm: segments.reduce((sum,segment)=>sum+segment.length,0),
      bounds_are_complete: arcs.length===0,
      bounds_scope: arcs.length ? 'linear segments only; arc extrema not computed' : 'complete linear path',
      bounds: ['x','y','z'].map(axis=>[Math.min(...vertices.map(v=>v[axis])),Math.max(...vertices.map(v=>v[axis]))]),
      endpoint: toolpath.getPosition()});
  }
  process.stdout.write(JSON.stringify(output)+'\n');
})().catch(error=>{process.stderr.write(String(error)+'\n');process.exitCode=1;});
