/**
 * Patches android/app/build.gradle after expo prebuild
 * to inject release signing config from environment variables.
 * Run: node scripts/patch-signing.js
 */

const fs = require('fs');
const path = require('path');

const gradlePath = path.join(__dirname, '..', 'android', 'app', 'build.gradle');

if (!fs.existsSync(gradlePath)) {
  console.error('[patch-signing] ERROR: android/app/build.gradle not found.');
  console.error('                Run expo prebuild first.');
  process.exit(1);
}

const { KEYSTORE_FILE, KEYSTORE_PASS, KEY_ALIAS, KEY_PASS } = process.env;

if (!KEYSTORE_FILE || !KEYSTORE_PASS || !KEY_ALIAS || !KEY_PASS) {
  console.error('[patch-signing] ERROR: Missing signing environment variables.');
  console.error('  Required: KEYSTORE_FILE, KEYSTORE_PASS, KEY_ALIAS, KEY_PASS');
  process.exit(1);
}

// Normalise Windows path separators for Groovy
const keystorePath = KEYSTORE_FILE.replace(/\\/g, '/');

let gradle = fs.readFileSync(gradlePath, 'utf8');

// Skip if already patched
if (gradle.includes('signingConfigs')) {
  console.log('[patch-signing] Already patched — skipping.');
  process.exit(0);
}

const signingBlock = `
    signingConfigs {
        release {
            storeFile file("${keystorePath}")
            storePassword "${KEYSTORE_PASS}"
            keyAlias "${KEY_ALIAS}"
            keyPassword "${KEY_PASS}"
        }
    }
`;

// Insert signingConfigs block before buildTypes
gradle = gradle.replace(
  /(\s*buildTypes\s*\{)/,
  `${signingBlock}\n$1`
);

// Add signingConfig to release buildType
gradle = gradle.replace(
  /(release\s*\{)/,
  `$1\n            signingConfig signingConfigs.release`
);

fs.writeFileSync(gradlePath, gradle, 'utf8');
console.log('[patch-signing] Release signing config injected into build.gradle');
