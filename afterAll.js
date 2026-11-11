const fs = require('fs');
const path = require('path');

// After the Windows files are built, also save copies with the version number in the name
// (My-Pics-Setup-1.10.2.exe). The plain names are used for the always-current download links,
// and the numbered ones keep older build setups working too.
exports.default = async function (result) {
  const version = require(path.join(__dirname, 'package.json')).version;
  const extra = [];
  for (const file of result.artifactPaths || []) {
    const name = path.basename(file);
    if (/^My-Pics-(Setup|Portable)\.exe$/.test(name)) {
      const copy = path.join(path.dirname(file), name.replace(/\.exe$/, '-' + version + '.exe'));
      fs.copyFileSync(file, copy);
      extra.push(copy);
    }
  }
  return extra;
};
