const { withAndroidManifest, withDangerousMod } = require('@expo/config-plugins');
const fs = require('fs');
const path = require('path');

const NETWORK_SECURITY_CONFIG_XML = `<?xml version="1.0" encoding="utf-8"?>
<network-security-config>
  <base-config cleartextTrafficPermitted="true">
    <trust-anchors>
      <certificates src="system" />
    </trust-anchors>
  </base-config>
</network-security-config>
`;

/**
 * Expo config plugin to permit HTTP (cleartext) traffic everywhere in the app
 * — API calls, WebView, downloads, etc. Writes:
 *   1. android:usesCleartextTraffic="true" on <application>
 *   2. android:networkSecurityConfig="@xml/network_security_config" on <application>
 *   3. android/app/src/main/res/xml/network_security_config.xml with cleartext=true
 *   4. <queries> entry allowing http scheme
 */
module.exports = function withCleartextTraffic(config) {
  // 1. Patch AndroidManifest.xml
  config = withAndroidManifest(config, (config) => {
    const manifest = config.modResults;
    const application = manifest.manifest.application?.[0];

    if (application) {
      application.$['android:usesCleartextTraffic'] = 'true';
      application.$['android:networkSecurityConfig'] = '@xml/network_security_config';
    }

    // Add http scheme to <queries> so the app can resolve http intents
    manifest.manifest.queries = manifest.manifest.queries || [];
    const hasHttp = manifest.manifest.queries.some((q) =>
      (q.intent || []).some((i) =>
        (i.data || []).some((d) => d.$?.['android:scheme'] === 'http')
      )
    );
    if (!hasHttp) {
      manifest.manifest.queries.push({
        intent: [
          {
            action: [{ $: { 'android:name': 'android.intent.action.VIEW' } }],
            category: [{ $: { 'android:name': 'android.intent.category.BROWSABLE' } }],
            data: [{ $: { 'android:scheme': 'http' } }],
          },
        ],
      });
    }

    return config;
  });

  // 2. Write network_security_config.xml into res/xml
  config = withDangerousMod(config, [
    'android',
    async (config) => {
      const xmlDir = path.join(
        config.modRequest.platformProjectRoot,
        'app',
        'src',
        'main',
        'res',
        'xml'
      );
      if (!fs.existsSync(xmlDir)) {
        fs.mkdirSync(xmlDir, { recursive: true });
      }
      const filePath = path.join(xmlDir, 'network_security_config.xml');
      fs.writeFileSync(filePath, NETWORK_SECURITY_CONFIG_XML, 'utf8');
      return config;
    },
  ]);

  return config;
};
