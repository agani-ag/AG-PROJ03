/**
 * Patches AndroidManifest.xml after expo prebuild to add
 * tools:replace="android:resource" on Firebase notification meta-data
 * entries that conflict with @react-native-firebase/messaging's manifest.
 * 
 * Run: node scripts/patch-notification-manifest.js
 */

const fs = require('fs');
const path = require('path');

const manifestPath = path.join(__dirname, '..', 'android', 'app', 'src', 'main', 'AndroidManifest.xml');

if (!fs.existsSync(manifestPath)) {
  console.error('[patch-notification] ERROR: AndroidManifest.xml not found.');
  console.error('                     Run expo prebuild first.');
  process.exit(1);
}

let manifest = fs.readFileSync(manifestPath, 'utf8');

const targets = [
  'com.google.firebase.messaging.default_notification_color',
  'com.google.firebase.messaging.default_notification_icon',
];

let patched = false;

for (const target of targets) {
  // Match the meta-data line for this target that does NOT already have tools:replace
  const regex = new RegExp(
    `(<meta-data\\s+android:name="${target.replace(/\./g, '\\.')}"\\s+android:resource="[^"]*")(/?>)`,
  );
  const match = manifest.match(regex);
  if (match && !match[0].includes('tools:replace')) {
    manifest = manifest.replace(regex, `$1 tools:replace="android:resource"$2`);
    console.log(`[patch-notification] Added tools:replace to ${target}`);
    patched = true;
  }
}

if (patched) {
  fs.writeFileSync(manifestPath, manifest, 'utf8');
  console.log('[patch-notification] AndroidManifest.xml patched successfully.');
} else {
  console.log('[patch-notification] No patching needed (already patched or entries not found).');
}
