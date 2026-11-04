const { execFileSync } = require('child_process');
const path = require('path');

// Mac: apply a free "ad-hoc" signature so the app can launch on Apple Silicon.
// (This is not Apple notarization, so macOS still asks the user to approve it once.)
exports.default = async function (context) {
  if (context.electronPlatformName !== 'darwin') return;
  const app = path.join(context.appOutDir, context.packager.appInfo.productFilename + '.app');
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', app], { stdio: 'inherit' });
};
