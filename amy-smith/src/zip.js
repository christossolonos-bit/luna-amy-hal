import { crc32, deflateRawSync } from 'node:zlib';

// A minimal .zip writer (no dependency): each entry deflated, plus the central directory.
// files: [{ path: 'src/index.js', data: Buffer }]. Paths use forward slashes.
export function zip(files) {
  const parts = [];
  const central = [];
  let offset = 0;
  const { time, date } = dosTime(new Date());
  for (const { path, data } of files) {
    const name = Buffer.from(path, 'utf8');
    const packed = deflateRawSync(data);
    const stored = packed.length >= data.length; // tiny files: deflate only makes them bigger
    const body = stored ? data : packed;
    const crc = crc32(data);
    const header = (sig, extra) => {
      const b = Buffer.alloc(extra ? 46 : 30);
      let o = 0;
      const u32 = (v) => { b.writeUInt32LE(v >>> 0, o); o += 4; };
      const u16 = (v) => { b.writeUInt16LE(v, o); o += 2; };
      u32(sig);
      if (extra) u16(20); // made by
      u16(20); // version needed
      u16(0x0800); // UTF-8 names
      u16(stored ? 0 : 8);
      u16(time); u16(date);
      u32(crc); u32(body.length); u32(data.length);
      u16(name.length); u16(0);
      if (extra) { u16(0); u16(0); u16(0); u32(0); u32(offset); }
      return b;
    };
    const local = header(0x04034b50, false);
    parts.push(local, name, body);
    central.push(header(0x02014b50, true), name);
    offset += local.length + name.length + body.length;
  }
  const dir = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(dir.length, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...parts, dir, end]);
}

function dosTime(d) {
  return {
    time: (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
    date: ((d.getFullYear() - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(),
  };
}
